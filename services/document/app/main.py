"""
SDIP Document Service — Secure file upload, encryption, integrity verification.
"""
import os
import hashlib
import uuid
import re
import json
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import aio_pika
from fastapi import FastAPI, UploadFile, File, Form, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from minio import Minio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ─── Helpers ─────────────────────────────────────
def _read_secret(env_var: str) -> str:
    path = os.getenv(env_var, "")
    if path and Path(path).exists():
        return Path(path).read_text().strip()
    return os.getenv(env_var.replace("_FILE", ""), "secret")

def _get_aes_key() -> bytes:
    hex_key = _read_secret("AES_KEY_FILE")
    return bytes.fromhex(hex_key)

def _get_jwt_public_key() -> str:
    path = os.getenv("JWT_PUBLIC_KEY_PATH", "")
    if path and Path(path).exists():
        return Path(path).read_text()
    return "dev-secret-key"

# ─── Configuration ───────────────────────────────
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "sdip-documents")
MAX_UPLOAD_SIZE = int(os.getenv("MAX_UPLOAD_SIZE", 52428800))  # 50 MB

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
ALLOWED_MIME_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/plain",
}
MAGIC_BYTES = {
    b"%PDF": ".pdf",
    b"PK\x03\x04": ".docx",
}

# Dangerous file signatures that should ALWAYS be rejected
BLOCKED_MAGIC_BYTES = [
    b"\x4d\x5a",           # MZ — Windows PE executable (.exe, .dll)
    b"\x7fELF",            # ELF — Linux executable
    b"\xfe\xed\xfa",       # Mach-O — macOS executable
    b"\xca\xfe\xba\xbe",   # Java class / Mach-O fat binary
    b"#!/",                 # Shell script (shebang)
]

# ─── App Lifespan ────────────────────────────────
db_pool = None
rabbit_connection = None
rabbit_channel = None
minio_client = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, rabbit_connection, rabbit_channel, minio_client

    # Database
    try:
        db_pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "sdip_docs"),
            user=os.getenv("DB_USER", "doc_svc"),
            password=_read_secret("DB_PASSWORD_FILE"),
            min_size=5, max_size=20,
        )
    except Exception as e:
        print(f"⚠ Database connection failed: {e}")

    # RabbitMQ
    try:
        rabbit_connection = await aio_pika.connect_robust(os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost/"))
        rabbit_channel = await rabbit_connection.channel()
        await rabbit_channel.declare_exchange("doc.events", aio_pika.ExchangeType.TOPIC, durable=True)
        await rabbit_channel.declare_exchange("audit.events", aio_pika.ExchangeType.FANOUT, durable=True)
    except Exception as e:
        print(f"⚠ RabbitMQ connection failed: {e}")

    # MinIO
    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=os.getenv("MINIO_ACCESS_KEY", "sdip-admin"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        secure=False,
    )
    if not minio_client.bucket_exists(MINIO_BUCKET):
        minio_client.make_bucket(MINIO_BUCKET)

    yield

    if db_pool:
        await db_pool.close()
    if rabbit_connection:
        await rabbit_connection.close()

app = FastAPI(title="SDIP Document Service", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── JWT Auth Dependency ─────────────────────────
from jose import jwt as jose_jwt, JWTError

async def get_current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing authorization header")
    token = auth.split(" ", 1)[1]
    public_key = _get_jwt_public_key()
    try:
        alg = "RS256" if "PUBLIC KEY" in public_key else "HS256"
        payload = jose_jwt.decode(token, public_key, algorithms=[alg], audience="sdip-services", issuer="sdip-auth")
        return payload
    except JWTError:
        raise HTTPException(401, "Invalid or expired token")

def require_role(allowed: list[str]):
    async def dep(user: dict = Depends(get_current_user)):
        if user.get("role") not in allowed:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return dep

# ─── Encryption Helpers ──────────────────────────
def encrypt_file(plaintext: bytes) -> tuple[bytes, bytes]:
    nonce = os.urandom(12)
    aesgcm = AESGCM(_get_aes_key())
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return nonce, ciphertext

def decrypt_file(nonce: bytes, ciphertext: bytes) -> bytes:
    aesgcm = AESGCM(_get_aes_key())
    return aesgcm.decrypt(nonce, ciphertext, None)

# ─── File Validation ─────────────────────────────
def validate_file(filename: str, content: bytes) -> str:
    # Extension check
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(422, f"File type '{ext}' not allowed. Allowed: {ALLOWED_EXTENSIONS}")

    # Size check
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"File too large. Maximum: {MAX_UPLOAD_SIZE // (1024*1024)} MB")

    # Blocked magic bytes check (dangerous file types)
    for blocked in BLOCKED_MAGIC_BYTES:
        if content[:len(blocked)] == blocked:
            raise HTTPException(422, "File contains a dangerous executable signature — upload rejected")

    # Magic bytes mismatch check
    for magic, expected_ext in MAGIC_BYTES.items():
        if content[:len(magic)] == magic and ext != expected_ext:
            raise HTTPException(422, "File content does not match extension (possible disguised file)")

    # Sanitize filename
    safe_name = re.sub(r'[^\w\s\-.]', '', filename)
    safe_name = re.sub(r'\.{2,}', '.', safe_name)
    return safe_name

# ─── Audit Helper ────────────────────────────────
async def publish_audit(action: str, user_id: str = None, severity: str = "info", **kwargs):
    if rabbit_channel:
        exchange = await rabbit_channel.get_exchange("audit.events")
        event = {"action": action, "user_id": user_id, "severity": severity, **kwargs,
                 "timestamp": __import__("datetime").datetime.utcnow().isoformat()}
        event["checksum"] = hashlib.sha256(json.dumps(event, default=str).encode()).hexdigest()
        await exchange.publish(aio_pika.Message(body=json.dumps(event, default=str).encode()), routing_key="")

async def publish_doc_event(routing_key: str, payload: dict):
    if rabbit_channel:
        exchange = await rabbit_channel.get_exchange("doc.events")
        msg = {"event_type": routing_key, "payload": payload,
               "timestamp": __import__("datetime").datetime.utcnow().isoformat()}
        await exchange.publish(aio_pika.Message(body=json.dumps(msg, default=str).encode()), routing_key=routing_key)

# ─── Routes ──────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "document-service"}

@app.post("/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(..., min_length=1, max_length=255),
    description: str = Form(""),
    user: dict = Depends(get_current_user),
):
    content = await file.read()
    safe_name = validate_file(file.filename, content)

    # Compute integrity hash
    sha256_hash = hashlib.sha256(content).hexdigest()

    # Encrypt
    nonce, ciphertext = encrypt_file(content)

    # Upload to MinIO
    doc_id = str(uuid.uuid4())
    storage_key = f"documents/{user['sub']}/{doc_id}/encrypted.bin"

    import io
    minio_client.put_object(MINIO_BUCKET, storage_key, io.BytesIO(ciphertext), len(ciphertext))

    # Store metadata
    row = await db_pool.fetchrow(
        """INSERT INTO documents (id, owner_id, title, description, file_name, file_size, mime_type,
           storage_key, encryption_iv, sha256_hash)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) RETURNING *""",
        uuid.UUID(doc_id), uuid.UUID(user["sub"]), title, description, safe_name,
        len(content), file.content_type or "application/octet-stream",
        storage_key, nonce, sha256_hash,
    )

    # Publish events
    await publish_doc_event("document.uploaded", {
        "document_id": doc_id, "owner_id": user["sub"], "storage_key": storage_key,
        "file_name": safe_name, "mime_type": file.content_type,
    })
    await publish_audit("document.upload", user_id=user["sub"], resource_type="document", resource_id=doc_id)

    return {"id": doc_id, "title": title, "file_name": safe_name,
            "file_size": len(content), "sha256_hash": sha256_hash, "created_at": str(row["created_at"])}

@app.get("/documents/")
async def list_documents(
    page: int = 1, limit: int = 20,
    user: dict = Depends(get_current_user),
):
    offset = (max(1, page) - 1) * min(100, max(1, limit))
    rows = await db_pool.fetch(
        """SELECT id, title, description, file_name, file_size, mime_type, sha256_hash, is_embedded, created_at
           FROM documents WHERE owner_id = $1 AND is_deleted = false
           ORDER BY created_at DESC LIMIT $2 OFFSET $3""",
        uuid.UUID(user["sub"]), limit, offset,
    )
    total = await db_pool.fetchval(
        "SELECT COUNT(*) FROM documents WHERE owner_id = $1 AND is_deleted = false",
        uuid.UUID(user["sub"]),
    )
    return {"documents": [dict(r) for r in rows], "total": total, "page": page}

@app.get("/documents/{doc_id}")
async def get_document(doc_id: str, user: dict = Depends(get_current_user)):
    row = await db_pool.fetchrow("SELECT * FROM documents WHERE id = $1 AND is_deleted = false", uuid.UUID(doc_id))
    if not row:
        raise HTTPException(404, "Document not found")
    if str(row["owner_id"]) != user["sub"] and user.get("role") != "admin":
        await publish_audit("security.unauthorized_access", user_id=user["sub"],
                            severity="warning", resource_type="document", resource_id=doc_id)
        raise HTTPException(403, "Access denied")
    return dict(row)

@app.get("/documents/{doc_id}/download")
async def download_document(doc_id: str, user: dict = Depends(get_current_user)):
    row = await db_pool.fetchrow("SELECT * FROM documents WHERE id = $1 AND is_deleted = false", uuid.UUID(doc_id))
    if not row:
        raise HTTPException(404, "Document not found")
    if str(row["owner_id"]) != user["sub"] and user.get("role") != "admin":
        raise HTTPException(403, "Access denied")

    # Fetch from MinIO
    response = minio_client.get_object(MINIO_BUCKET, row["storage_key"])
    ciphertext = response.read()
    response.close()

    # Decrypt
    plaintext = decrypt_file(bytes(row["encryption_iv"]), ciphertext)

    # Verify integrity
    computed_hash = hashlib.sha256(plaintext).hexdigest()
    if computed_hash != row["sha256_hash"]:
        await publish_audit("security.integrity_failure", severity="critical",
                            resource_type="document", resource_id=doc_id)
        raise HTTPException(500, "Integrity verification failed — file may be tampered")

    await publish_audit("document.download", user_id=user["sub"], resource_type="document", resource_id=doc_id)

    import io
    return StreamingResponse(
        io.BytesIO(plaintext),
        media_type=row["mime_type"],
        headers={"Content-Disposition": f'attachment; filename="{row["file_name"]}"'},
    )

@app.delete("/documents/{doc_id}")
async def delete_document(doc_id: str, user: dict = Depends(get_current_user)):
    row = await db_pool.fetchrow("SELECT * FROM documents WHERE id = $1 AND is_deleted = false", uuid.UUID(doc_id))
    if not row:
        raise HTTPException(404, "Document not found")
    if str(row["owner_id"]) != user["sub"] and user.get("role") != "admin":
        raise HTTPException(403, "Access denied")

    await db_pool.execute(
        "UPDATE documents SET is_deleted = true, deleted_at = NOW() WHERE id = $1", uuid.UUID(doc_id)
    )
    await publish_doc_event("document.deleted", {"document_id": doc_id, "storage_key": row["storage_key"]})
    await publish_audit("document.delete", user_id=user["sub"], resource_type="document", resource_id=doc_id)

    return {"message": "Document deleted"}

@app.get("/documents/admin/all")
async def list_all_documents(page: int = 1, limit: int = 20, user: dict = Depends(require_role(["admin"]))):
    offset = (max(1, page) - 1) * min(100, max(1, limit))
    rows = await db_pool.fetch(
        """SELECT id, owner_id, title, file_name, file_size, mime_type, created_at
           FROM documents WHERE is_deleted = false ORDER BY created_at DESC LIMIT $1 OFFSET $2""",
        limit, offset,
    )
    total = await db_pool.fetchval("SELECT COUNT(*) FROM documents WHERE is_deleted = false")
    return {"documents": [dict(r) for r in rows], "total": total, "page": page}
