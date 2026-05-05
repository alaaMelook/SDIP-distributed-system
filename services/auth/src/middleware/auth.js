const jwt = require('jsonwebtoken');
const fs = require('fs');

/**
 * JWT Authentication Middleware
 * Verifies access token from Authorization header.
 */
function authenticate(req, res, next) {
  const authHeader = req.headers.authorization;
  if (!authHeader || !authHeader.startsWith('Bearer ')) {
    return res.status(401).json({ error: { code: 'MISSING_TOKEN', message: 'Authorization header required' } });
  }

  const token = authHeader.split(' ')[1];
  const publicKey = req.app.get('jwtPublicKey');

  try {
    const decoded = jwt.verify(token, publicKey, {
      algorithms: [publicKey.includes('PUBLIC KEY') ? 'RS256' : 'HS256'],
      issuer: 'sdip-auth',
      audience: 'sdip-services',
    });
    req.user = decoded;
    next();
  } catch (err) {
    // Publish audit event for invalid token
    const channel = req.app.get('rabbit')?.();
    if (channel) {
      const crypto = require('crypto');
      const event = {
        action: 'security.invalid_token', severity: 'warning',
        ip_address: req.ip, user_agent: req.headers['user-agent'],
        details: { error: err.message },
        timestamp: new Date().toISOString()
      };
      event.checksum = crypto.createHash('sha256').update(JSON.stringify(event)).digest('hex');
      channel.publish('audit.events', '', Buffer.from(JSON.stringify(event)));
    }

    if (err.name === 'TokenExpiredError') {
      return res.status(401).json({ error: { code: 'TOKEN_EXPIRED', message: 'Access token expired' } });
    }
    return res.status(401).json({ error: { code: 'INVALID_TOKEN', message: 'Invalid access token' } });
  }
}

/**
 * Role-Based Access Control Middleware
 * @param {string[]} allowedRoles - Array of allowed roles
 */
function requireRole(allowedRoles) {
  return (req, res, next) => {
    if (!req.user || !allowedRoles.includes(req.user.role)) {
      return res.status(403).json({ error: { code: 'FORBIDDEN', message: 'Insufficient permissions' } });
    }
    next();
  };
}

module.exports = { authenticate, requireRole };
