const express = require('express');
const router = express.Router();
const { authenticate, requireRole } = require('../middleware/auth');

// ─── GET /auth/users — List all users (Admin only) ──
router.get('/', authenticate, requireRole(['admin']), async (req, res) => {
  const db = req.app.get('db');
  const page = Math.max(1, parseInt(req.query.page) || 1);
  const limit = Math.min(100, Math.max(1, parseInt(req.query.limit) || 20));
  const offset = (page - 1) * limit;

  try {
    const [users, count] = await Promise.all([
      db.query(
        'SELECT id, email, role, display_name, is_active, created_at FROM users ORDER BY created_at DESC LIMIT $1 OFFSET $2',
        [limit, offset]
      ),
      db.query('SELECT COUNT(*) FROM users'),
    ]);

    res.json({ users: users.rows, total: parseInt(count.rows[0].count), page, limit });
  } catch (err) {
    console.error('List users error:', err);
    res.status(500).json({ error: { code: 'INTERNAL_ERROR', message: 'Failed to list users' } });
  }
});

// ─── PUT /auth/users/:id/role — Change user role (Admin only) ──
router.put('/:id/role', authenticate, requireRole(['admin']), async (req, res) => {
  const { role } = req.body;
  if (!role || !['admin', 'user'].includes(role)) {
    return res.status(400).json({ error: { code: 'INVALID_ROLE', message: 'Role must be admin or user' } });
  }

  const db = req.app.get('db');
  const { id } = req.params;

  try {
    const result = await db.query(
      'UPDATE users SET role = $1, updated_at = NOW() WHERE id = $2 RETURNING id, email, role, display_name',
      [role, id]
    );
    if (result.rows.length === 0) {
      return res.status(404).json({ error: { code: 'NOT_FOUND', message: 'User not found' } });
    }

    // Publish audit event
    const channel = req.app.get('rabbit')();
    if (channel) {
      const crypto = require('crypto');
      const event = {
        action: 'admin.role_change', user_id: req.user.sub, severity: 'warning',
        resource_type: 'user', resource_id: id, ip_address: req.ip,
        details: { new_role: role, target_user: id },
        timestamp: new Date().toISOString()
      };
      event.checksum = crypto.createHash('sha256').update(JSON.stringify(event)).digest('hex');
      channel.publish('audit.events', '', Buffer.from(JSON.stringify(event)));
    }

    res.json(result.rows[0]);
  } catch (err) {
    console.error('Role change error:', err);
    res.status(500).json({ error: { code: 'INTERNAL_ERROR', message: 'Failed to update role' } });
  }
});

module.exports = router;
