/**
 * SDIP — Create initial admin user
 * Usage: docker compose exec auth-service node scripts/create-admin.js
 */
const { Pool } = require('pg');
const bcrypt = require('bcryptjs');
const fs = require('fs');
const readline = require('readline');

function readSecret(envVar) {
  const filePath = process.env[envVar];
  if (filePath && fs.existsSync(filePath)) {
    return fs.readFileSync(filePath, 'utf8').trim();
  }
  return process.env[envVar.replace('_FILE', '')] || '';
}

const pool = new Pool({
  host: process.env.DB_HOST || 'localhost',
  port: parseInt(process.env.DB_PORT || '5432'),
  database: process.env.DB_NAME || 'sdip_auth',
  user: process.env.DB_USER || 'auth_svc',
  password: readSecret('DB_PASSWORD_FILE'),
});

async function createAdmin() {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const ask = (q) => new Promise((resolve) => rl.question(q, resolve));

  try {
    const email = await ask('Admin email: ');
    const password = await ask('Admin password (min 8 chars): ');
    const displayName = await ask('Display name: ');

    if (password.length < 8) {
      console.error('✗ Password must be at least 8 characters');
      process.exit(1);
    }

    const rounds = parseInt(process.env.BCRYPT_ROUNDS || '12');
    const hash = await bcrypt.hash(password, rounds);

    const result = await pool.query(
      `INSERT INTO users (email, password_hash, role, display_name)
       VALUES ($1, $2, 'admin', $3)
       ON CONFLICT (email) DO UPDATE SET role = 'admin', password_hash = $2
       RETURNING id, email, role, display_name`,
      [email, hash, displayName]
    );

    console.log('✅ Admin user created/updated:');
    console.log(result.rows[0]);
  } catch (err) {
    console.error('✗ Error:', err.message);
  } finally {
    rl.close();
    await pool.end();
  }
}

createAdmin();
