const express = require('express');
const router = express.Router();
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const { body, validationResult } = require('express-validator');
const { authenticate } = require('../middleware/auth');

// ─── Validation Rules ───────────────────────────
const registerValidation = [
  body('email').isEmail().normalizeEmail(),
  body('password').isStrongPassword({
    minLength: 8, minLowercase: 1, minUppercase: 1, minNumbers: 1, minSymbols: 1
  }).withMessage('Password must be 8+ chars with uppercase, lowercase, number, and symbol'),
  body('display_name').trim().isLength({ min: 1, max: 255 }).escape(),
];

const loginValidation = [
  body('email').isEmail().normalizeEmail(),
  body('password').notEmpty(),
];

// ─── Helper: Publish audit event ────────────────
function publishAudit(app, event) {
  const channel = app.get('rabbit')();
  if (channel) {
    const checksum = crypto.createHash('sha256')
      .update(JSON.stringify(event)).digest('hex');
    channel.publish('audit.events', '', Buffer.from(JSON.stringify({
      ...event, checksum, timestamp: new Date().toISOString()
    })));
  }
}

// ─── Helper: Generate JWT pair ──────────────────
function generateTokens(user, privateKey) {
  const accessToken = jwt.sign(
    { sub: user.id, role: user.role, email: user.email },
    privateKey,
    { algorithm: privateKey.includes('PRIVATE KEY') ? 'RS256' : 'HS256', expiresIn: '15m', issuer: 'sdip-auth', audience: 'sdip-services' }
  );
  const refreshToken = crypto.randomBytes(64).toString('hex');
  return { accessToken, refreshToken };
}

// ─── POST /auth/register ────────────────────────
router.post('/register', registerValidation, async (req, res) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ error: { code: 'VALIDATION_ERROR', details: errors.array() } });
  }

  const { email, password, display_name } = req.body;
  const db = req.app.get('db');

  try {
    // Check if user exists
    const existing = await db.query('SELECT id FROM users WHERE email = $1', [email]);
    if (existing.rows.length > 0) {
      return res.status(409).json({ error: { code: 'USER_EXISTS', message: 'Email already registered' } });
    }

    // Hash password
    const rounds = parseInt(process.env.BCRYPT_ROUNDS || '12');
    const passwordHash = await bcrypt.hash(password, rounds);

    // Insert user
    const result = await db.query(
      `INSERT INTO users (email, password_hash, display_name) VALUES ($1, $2, $3) RETURNING id, email, role, display_name, created_at`,
      [email, passwordHash, display_name]
    );
    const user = result.rows[0];

    // Generate tokens
    const privateKey = req.app.get('jwtPrivateKey');
    const { accessToken, refreshToken } = generateTokens(user, privateKey);

    // Store refresh token hash
    const tokenHash = crypto.createHash('sha256').update(refreshToken).digest('hex');
    const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
    await db.query(
      `INSERT INTO refresh_tokens (user_id, token_hash, device_info, expires_at) VALUES ($1, $2, $3, $4)`,
      [user.id, tokenHash, req.headers['user-agent'] || 'unknown', expiresAt]
    );

    publishAudit(req.app, {
      action: 'auth.register', user_id: user.id, severity: 'info',
      ip_address: req.ip, user_agent: req.headers['user-agent']
    });

    res.status(201).json({ user, access_token: accessToken, refresh_token: refreshToken });
  } catch (err) {
    console.error('Register error:', err);
    res.status(500).json({ error: { code: 'INTERNAL_ERROR', message: 'Registration failed' } });
  }
});

// ─── POST /auth/login ───────────────────────────
router.post('/login', loginValidation, async (req, res) => {
  const errors = validationResult(req);
  if (!errors.isEmpty()) {
    return res.status(400).json({ error: { code: 'VALIDATION_ERROR', details: errors.array() } });
  }

  const { email, password } = req.body;
  const db = req.app.get('db');

  try {
    const result = await db.query('SELECT * FROM users WHERE email = $1', [email]);
    if (result.rows.length === 0) {
      return res.status(401).json({ error: { code: 'INVALID_CREDENTIALS', message: 'Invalid email or password' } });
    }

    const user = result.rows[0];

    // Check account lockout
    if (user.locked_until && new Date(user.locked_until) > new Date()) {
      publishAudit(req.app, {
        action: 'auth.login_locked', user_id: user.id, severity: 'warning',
        ip_address: req.ip, details: { locked_until: user.locked_until }
      });
      return res.status(423).json({ error: { code: 'ACCOUNT_LOCKED', message: 'Account temporarily locked' } });
    }

    // Verify password
    if (!user.password_hash || !(await bcrypt.compare(password, user.password_hash))) {
      const attempts = user.failed_login_attempts + 1;
      const lockUntil = attempts >= 5 ? new Date(Date.now() + 15 * 60 * 1000) : null;

      await db.query(
        'UPDATE users SET failed_login_attempts = $1, locked_until = $2 WHERE id = $3',
        [attempts, lockUntil, user.id]
      );

      publishAudit(req.app, {
        action: 'auth.login_failed', user_id: user.id, severity: attempts >= 5 ? 'critical' : 'warning',
        ip_address: req.ip, details: { attempts }
      });

      if (lockUntil) {
        return res.status(423).json({ error: { code: 'ACCOUNT_LOCKED', message: 'Too many attempts. Account locked for 15 minutes.' } });
      }
      return res.status(401).json({ error: { code: 'INVALID_CREDENTIALS', message: 'Invalid email or password' } });
    }

    // Reset failed attempts on success
    await db.query('UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = $1', [user.id]);

    // Generate tokens
    const privateKey = req.app.get('jwtPrivateKey');
    const { accessToken, refreshToken } = generateTokens(user, privateKey);

    // Store refresh token
    const tokenHash = crypto.createHash('sha256').update(refreshToken).digest('hex');
    const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
    await db.query(
      `INSERT INTO refresh_tokens (user_id, token_hash, device_info, expires_at) VALUES ($1, $2, $3, $4)`,
      [user.id, tokenHash, req.headers['user-agent'] || 'unknown', expiresAt]
    );

    publishAudit(req.app, {
      action: 'auth.login_success', user_id: user.id, severity: 'info',
      ip_address: req.ip, user_agent: req.headers['user-agent']
    });

    res.json({
      access_token: accessToken, refresh_token: refreshToken,
      user: { id: user.id, email: user.email, role: user.role, display_name: user.display_name }
    });
  } catch (err) {
    console.error('Login error:', err);
    res.status(500).json({ error: { code: 'INTERNAL_ERROR', message: 'Login failed' } });
  }
});

// ─── POST /auth/refresh ─────────────────────────
router.post('/refresh', async (req, res) => {
  const { refresh_token } = req.body;
  if (!refresh_token) {
    return res.status(400).json({ error: { code: 'MISSING_TOKEN', message: 'Refresh token required' } });
  }

  const db = req.app.get('db');
  const tokenHash = crypto.createHash('sha256').update(refresh_token).digest('hex');

  try {
    const result = await db.query(
      `SELECT rt.*, u.id as uid, u.email, u.role, u.display_name
       FROM refresh_tokens rt JOIN users u ON rt.user_id = u.id
       WHERE rt.token_hash = $1 AND rt.revoked_at IS NULL AND rt.expires_at > NOW()`,
      [tokenHash]
    );

    if (result.rows.length === 0) {
      return res.status(401).json({ error: { code: 'INVALID_TOKEN', message: 'Invalid or expired refresh token' } });
    }

    const row = result.rows[0];
    // Revoke old token
    await db.query('UPDATE refresh_tokens SET revoked_at = NOW() WHERE id = $1', [row.id]);

    // Issue new pair
    const user = { id: row.uid, email: row.email, role: row.role };
    const privateKey = req.app.get('jwtPrivateKey');
    const { accessToken, refreshToken: newRefresh } = generateTokens(user, privateKey);

    const newHash = crypto.createHash('sha256').update(newRefresh).digest('hex');
    const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
    await db.query(
      `INSERT INTO refresh_tokens (user_id, token_hash, device_info, expires_at) VALUES ($1, $2, $3, $4)`,
      [row.uid, newHash, req.headers['user-agent'] || 'unknown', expiresAt]
    );

    res.json({ access_token: accessToken, refresh_token: newRefresh });
  } catch (err) {
    console.error('Refresh error:', err);
    res.status(500).json({ error: { code: 'INTERNAL_ERROR', message: 'Token refresh failed' } });
  }
});

// ─── POST /auth/logout ──────────────────────────
router.post('/logout', authenticate, async (req, res) => {
  const { refresh_token } = req.body;
  if (!refresh_token) {
    return res.status(400).json({ error: { code: 'MISSING_TOKEN', message: 'Refresh token required' } });
  }

  const db = req.app.get('db');
  const tokenHash = crypto.createHash('sha256').update(refresh_token).digest('hex');

  await db.query('UPDATE refresh_tokens SET revoked_at = NOW() WHERE token_hash = $1', [tokenHash]);

  publishAudit(req.app, {
    action: 'auth.logout', user_id: req.user.sub, severity: 'info', ip_address: req.ip
  });

  res.json({ message: 'Logged out successfully' });
});

// ─── GET /auth/me ───────────────────────────────
router.get('/me', authenticate, async (req, res) => {
  const db = req.app.get('db');
  const result = await db.query(
    'SELECT id, email, role, display_name, avatar_url, created_at FROM users WHERE id = $1',
    [req.user.sub]
  );
  if (result.rows.length === 0) {
    return res.status(404).json({ error: { code: 'NOT_FOUND', message: 'User not found' } });
  }
  res.json(result.rows[0]);
});

module.exports = router;
