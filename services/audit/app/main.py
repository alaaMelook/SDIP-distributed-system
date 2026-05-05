"""
SDIP Audit Service — Centralized logging, audit trail, security event monitoring.
Consumes events from RabbitMQ and exposes admin-only query APIs.
"""
import os
import json
import uuid
import hashlib
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime

import asyncpg
import aio_pika
from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from jose import jwt as jose_jwt, JWTError

def _read_secret(env_var: str) -> str:
    path = os.getenv(env_var, "")
    if path and Path(path).exists():
        return Path(path).read_text().strip()
    return os.getenv(env_var.replace("_FILE", ""), "secret")

def _get_jwt_public_key() -> str:
    path = os.getenv("JWT_PUBLIC_KEY_PATH", "")
    if path and Path(path).exists():
        return Path(path).read_text()
    return "dev-secret-key"

db_pool = None
rabbit_connection = None

async def consume_audit_events():
    """Background task: consume from audit.events fanout exchange."""
    global db_pool
    while True:
        try:
            conn = await aio_pika.connect_robust(os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost/"))
            channel = await conn.channel()
            exchange = await channel.declare_exchange("audit.events", aio_pika.ExchangeType.FANOUT, durable=True)
            queue = await channel.declare_queue("audit.log", durable=True)
            await queue.bind(exchange)
            await channel.set_qos(prefetch_count=10)

            print("✓ Audit consumer connected, waiting for events...")

            async for message in queue:
                async with message.process():
                    try:
                        event = json.loads(message.body)
                        await insert_audit_log(event)
                    except Exception as e:
                        print(f"✗ Failed to process audit event: {e}")
        except Exception as e:
            print(f"⚠ RabbitMQ consumer error: {e}, reconnecting in 5s...")
            await asyncio.sleep(5)

async def insert_audit_log(event: dict):
    """Insert an audit log entry into PostgreSQL."""
    checksum = event.get("checksum", hashlib.sha256(json.dumps(event, default=str).encode()).hexdigest())
    await db_pool.execute(
        """INSERT INTO audit_logs (user_id, action, resource_type, resource_id,
           ip_address, user_agent, details, severity, checksum)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
        uuid.UUID(event["user_id"]) if event.get("user_id") else None,
        event.get("action", "unknown"),
        event.get("resource_type"),
        uuid.UUID(event["resource_id"]) if event.get("resource_id") else None,
        event.get("ip_address"),
        event.get("user_agent"),
        json.dumps(event.get("details", {})),
        event.get("severity", "info"),
        checksum,
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool
    db_pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "sdip_audit"),
        user=os.getenv("DB_USER", "audit_svc"),
        password=_read_secret("DB_PASSWORD_FILE"),
        min_size=2, max_size=10,
    )
    # Start background consumer
    consumer_task = asyncio.create_task(consume_audit_events())
    yield
    consumer_task.cancel()
    if db_pool:
        await db_pool.close()

app = FastAPI(title="SDIP Audit Service", lifespan=lifespan)

# CORS — allow dashboard to call auth service
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve dashboard static files
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# ─── Auth Dependency ─────────────────────────────
async def get_current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing authorization header")
    token = auth.split(" ", 1)[1]
    public_key = _get_jwt_public_key()
    try:
        alg = "RS256" if "PUBLIC KEY" in public_key else "HS256"
        return jose_jwt.decode(token, public_key, algorithms=[alg], audience="sdip-services", issuer="sdip-auth")
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")

def require_admin(user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user

# ─── Routes ──────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "audit-service"}

@app.get("/dashboard")
async def dashboard():
    """Serve the monitoring dashboard."""
    html_path = STATIC_DIR / "dashboard.html"
    if html_path.exists():
        return FileResponse(str(html_path), media_type="text/html")
    raise HTTPException(404, "Dashboard not found")

@app.get("/audit/logs")
async def get_logs(
    action: str = None,
    severity: str = None,
    from_date: str = None,
    to_date: str = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_admin),
):
    conditions = []
    params = []
    idx = 1

    if action:
        conditions.append(f"action = ${idx}")
        params.append(action)
        idx += 1
    if severity:
        conditions.append(f"severity = ${idx}")
        params.append(severity)
        idx += 1
    if from_date:
        conditions.append(f"timestamp >= ${idx}")
        params.append(datetime.fromisoformat(from_date))
        idx += 1
    if to_date:
        conditions.append(f"timestamp <= ${idx}")
        params.append(datetime.fromisoformat(to_date))
        idx += 1

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * limit

    rows = await db_pool.fetch(
        f"SELECT * FROM audit_logs {where} ORDER BY timestamp DESC LIMIT ${idx} OFFSET ${idx+1}",
        *params, limit, offset,
    )
    total = await db_pool.fetchval(f"SELECT COUNT(*) FROM audit_logs {where}", *params)

    return {"logs": [dict(r) for r in rows], "total": total, "page": page}

@app.get("/audit/logs/user/{user_id}")
async def get_user_logs(
    user_id: str,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_admin),
):
    offset = (page - 1) * limit
    rows = await db_pool.fetch(
        "SELECT * FROM audit_logs WHERE user_id = $1 ORDER BY timestamp DESC LIMIT $2 OFFSET $3",
        uuid.UUID(user_id), limit, offset,
    )
    total = await db_pool.fetchval("SELECT COUNT(*) FROM audit_logs WHERE user_id = $1", uuid.UUID(user_id))
    return {"logs": [dict(r) for r in rows], "total": total, "page": page}

@app.get("/audit/security-events")
async def get_security_events(
    severity: str = "warning",
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_admin),
):
    offset = (page - 1) * limit
    rows = await db_pool.fetch(
        """SELECT * FROM audit_logs
           WHERE action LIKE 'security.%' AND severity >= $1
           ORDER BY timestamp DESC LIMIT $2 OFFSET $3""",
        severity, limit, offset,
    )
    return {"events": [dict(r) for r in rows]}

@app.get("/audit/stats")
async def get_stats(period: str = "day", user: dict = Depends(require_admin)):
    interval = {"hour": "1 hour", "day": "1 day", "week": "7 days", "month": "30 days"}.get(period, "1 day")
    rows = await db_pool.fetch(
        f"""SELECT action, severity, COUNT(*) as count
            FROM audit_logs WHERE timestamp > NOW() - INTERVAL '{interval}'
            GROUP BY action, severity ORDER BY count DESC LIMIT 50""")
    total = await db_pool.fetchval(
        f"SELECT COUNT(*) FROM audit_logs WHERE timestamp > NOW() - INTERVAL '{interval}'")
    return {"stats": [dict(r) for r in rows], "total_events": total, "period": period}
