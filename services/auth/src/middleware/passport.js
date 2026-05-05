const passport = require('passport');
const GoogleStrategy = require('passport-google-oauth20').Strategy;
const GitHubStrategy = require('passport-github2').Strategy;
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const fs = require('fs');

function readSecret(filePath) {
  if (filePath && fs.existsSync(filePath)) {
    return fs.readFileSync(filePath, 'utf8').trim();
  }
  return '';
}

/**
 * Initialize Passport OAuth Strategies
 */
function initPassport(app, pool) {
  app.use(passport.initialize());

  const privateKey = app.get('jwtPrivateKey');

  // ─── Helper: Find or create OAuth user ────────
  async function findOrCreateUser(provider, profile) {
    const oauthId = profile.id;
    const email = profile.emails?.[0]?.value || `${oauthId}@${provider}.oauth`;
    const displayName = profile.displayName || email.split('@')[0];
    const avatar = profile.photos?.[0]?.value || null;

    // Check if OAuth user exists
    let result = await pool.query(
      'SELECT * FROM users WHERE oauth_provider = $1 AND oauth_id = $2',
      [provider, oauthId]
    );

    if (result.rows.length > 0) {
      return result.rows[0];
    }

    // Check if email user exists (link accounts)
    result = await pool.query('SELECT * FROM users WHERE email = $1', [email]);
    if (result.rows.length > 0) {
      await pool.query(
        'UPDATE users SET oauth_provider = $1, oauth_id = $2, avatar_url = COALESCE(avatar_url, $3) WHERE id = $4',
        [provider, oauthId, avatar, result.rows[0].id]
      );
      return { ...result.rows[0], oauth_provider: provider, oauth_id: oauthId };
    }

    // Create new user
    result = await pool.query(
      `INSERT INTO users (email, display_name, avatar_url, oauth_provider, oauth_id)
       VALUES ($1, $2, $3, $4, $5) RETURNING *`,
      [email, displayName, avatar, provider, oauthId]
    );
    return result.rows[0];
  }

  // ─── OAuth callback handler ───────────────────
  function oauthCallback(provider) {
    return async (req, res) => {
      const user = req.user;
      const alg = privateKey.includes('RSA') ? 'RS256' : 'HS256';

      const accessToken = jwt.sign(
        { sub: user.id, role: user.role, email: user.email },
        privateKey,
        { algorithm: alg, expiresIn: '15m', issuer: 'sdip-auth', audience: 'sdip-services' }
      );
      const refreshToken = crypto.randomBytes(64).toString('hex');
      const tokenHash = crypto.createHash('sha256').update(refreshToken).digest('hex');
      const expiresAt = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);

      await pool.query(
        `INSERT INTO refresh_tokens (user_id, token_hash, device_info, expires_at) VALUES ($1, $2, $3, $4)`,
        [user.id, tokenHash, `OAuth-${provider}`, expiresAt]
      );

      // Redirect with tokens (in production, use secure cookies or fragment)
      res.redirect(`/?access_token=${accessToken}&refresh_token=${refreshToken}`);
    };
  }

  // ─── Google OAuth Strategy ────────────────────
  const googleSecret = readSecret(process.env.OAUTH_GOOGLE_CLIENT_SECRET_FILE);
  if (process.env.OAUTH_GOOGLE_CLIENT_ID && googleSecret) {
    passport.use(new GoogleStrategy({
      clientID: process.env.OAUTH_GOOGLE_CLIENT_ID,
      clientSecret: googleSecret,
      callbackURL: '/auth/oauth/google/callback',
      passReqToCallback: true,
    }, async (req, accessToken, refreshToken, profile, done) => {
      try {
        const user = await findOrCreateUser('google', profile);
        done(null, user);
      } catch (err) { done(err); }
    }));

    app.get('/auth/oauth/google', passport.authenticate('google', { scope: ['profile', 'email'], session: false }));
    app.get('/auth/oauth/google/callback',
      passport.authenticate('google', { failureRedirect: '/auth/login', session: false }),
      oauthCallback('google')
    );
  }

  // ─── GitHub OAuth Strategy ────────────────────
  const githubSecret = readSecret(process.env.OAUTH_GITHUB_CLIENT_SECRET_FILE);
  if (process.env.OAUTH_GITHUB_CLIENT_ID && githubSecret) {
    passport.use(new GitHubStrategy({
      clientID: process.env.OAUTH_GITHUB_CLIENT_ID,
      clientSecret: githubSecret,
      callbackURL: '/auth/oauth/github/callback',
      passReqToCallback: true,
    }, async (req, accessToken, refreshToken, profile, done) => {
      try {
        const user = await findOrCreateUser('github', profile);
        done(null, user);
      } catch (err) { done(err); }
    }));

    app.get('/auth/oauth/github', passport.authenticate('github', { scope: ['user:email'], session: false }));
    app.get('/auth/oauth/github/callback',
      passport.authenticate('github', { failureRedirect: '/auth/login', session: false }),
      oauthCallback('github')
    );
  }
}

module.exports = { initPassport };
