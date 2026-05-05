const express = require('express');
const helmet = require('helmet');
const cors = require('cors');
const morgan = require('morgan');
const { Pool } = require('pg');
const amqp = require('amqplib');
const fs = require('fs');
const authRoutes = require('./routes/auth');
const userRoutes = require('./routes/users');
const { initPassport } = require('./middleware/passport');

const app = express();
const PORT = process.env.PORT || 3001;

// ─── Read secrets from files ────────────────────
function readSecret(envVar) {
  const filePath = process.env[envVar];
  if (filePath && fs.existsSync(filePath)) {
    return fs.readFileSync(filePath, 'utf8').trim();
  }
  return process.env[envVar.replace('_FILE', '')] || '';
}

// ─── Database Connection ────────────────────────
const pool = new Pool({
  host: process.env.DB_HOST || 'localhost',
  port: parseInt(process.env.DB_PORT || '5432'),
  database: process.env.DB_NAME || 'sdip_auth',
  user: process.env.DB_USER || 'auth_svc',
  password: readSecret('DB_PASSWORD_FILE'),
  max: 20,
  idleTimeoutMillis: 30000,
});

// ─── RabbitMQ Connection ────────────────────────
let rabbitChannel = null;
async function connectRabbitMQ() {
  try {
    const conn = await amqp.connect(process.env.RABBITMQ_URL);
    rabbitChannel = await conn.createChannel();
    await rabbitChannel.assertExchange('audit.events', 'fanout', { durable: true });
    console.log('✓ Connected to RabbitMQ');
  } catch (err) {
    console.error('✗ RabbitMQ connection failed, retrying in 5s...', err.message);
    setTimeout(connectRabbitMQ, 5000);
  }
}

// ─── JWT Keys ───────────────────────────────────
const jwtPrivateKey = fs.existsSync(process.env.JWT_PRIVATE_KEY_PATH || '')
  ? fs.readFileSync(process.env.JWT_PRIVATE_KEY_PATH, 'utf8')
  : 'dev-secret-key';
const jwtPublicKey = fs.existsSync(process.env.JWT_PUBLIC_KEY_PATH || '')
  ? fs.readFileSync(process.env.JWT_PUBLIC_KEY_PATH, 'utf8')
  : 'dev-secret-key';

// ─── Middleware ─────────────────────────────────
app.use(helmet());
app.use(cors({ origin: true, credentials: true }));
app.use(express.json({ limit: '1mb' }));
app.use(morgan('combined'));

// ─── Make dependencies available to routes ──────
app.set('db', pool);
app.set('rabbit', () => rabbitChannel);
app.set('jwtPrivateKey', jwtPrivateKey);
app.set('jwtPublicKey', jwtPublicKey);

// ─── Initialize Passport (OAuth) ───────────────
initPassport(app, pool);

// ─── Routes ─────────────────────────────────────
app.use('/auth', authRoutes);
app.use('/auth/users', userRoutes);

// ─── Health Check ───────────────────────────────
app.get('/health', async (req, res) => {
  try {
    await pool.query('SELECT 1');
    res.json({ status: 'ok', service: 'auth-service' });
  } catch (err) {
    res.status(503).json({ status: 'unhealthy', error: err.message });
  }
});

// ─── Global Error Handler ───────────────────────
app.use((err, req, res, next) => {
  console.error('Unhandled error:', err);
  res.status(500).json({
    error: { code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' }
  });
});

// ─── Start Server ───────────────────────────────
app.listen(PORT, async () => {
  console.log(`🔐 Auth Service running on port ${PORT}`);
  await connectRabbitMQ();
});

module.exports = app;
