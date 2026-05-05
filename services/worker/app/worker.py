"""
SDIP Worker Service — RabbitMQ consumer for async document processing and embedding.
"""
import os
import json
import uuid
import hashlib
import time
import io
from pathlib import Path

import pika
import psycopg2
from minio import Minio
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, VectorParams, Distance
from sentence_transformers import SentenceTransformer

# ─── Config ──────────────────────────────────────
def _read_secret(env_var: str) -> str:
    path = os.getenv(env_var, "")
    if path and Path(path).exists():
        return Path(path).read_text().strip()
    return os.getenv(env_var.replace("_FILE", ""), "secret")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
    "dbname": os.getenv("DB_NAME", "sdip_docs"),
    "user": os.getenv("DB_USER", "doc_svc"),
    "password": _read_secret("DB_PASSWORD_FILE"),
}
RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "sdip-documents")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
COLLECTION_NAME = "sdip_documents"
CHUNK_SIZE = 512  # tokens per chunk (approx)

# ─── Clients ─────────────────────────────────────
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=os.getenv("MINIO_ACCESS_KEY", "sdip-admin"),
    secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
    secure=False,
)
qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

print(f"Loading embedding model: {EMBEDDING_MODEL}...")
embedder = SentenceTransformer(EMBEDDING_MODEL)
print("✓ Model loaded")

# Ensure Qdrant collection
try:
    qdrant.get_collection(COLLECTION_NAME)
except Exception:
    qdrant.create_collection(COLLECTION_NAME, vectors_config=VectorParams(size=384, distance=Distance.COSINE))

def get_db():
    return psycopg2.connect(**DB_CONFIG)

def get_aes_key() -> bytes:
    return bytes.fromhex(_read_secret("AES_KEY_FILE"))

# ─── Text Extraction ────────────────────────────
def extract_text(content: bytes, mime_type: str) -> str:
    if "pdf" in mime_type:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(content))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    elif "wordprocessingml" in mime_type or "docx" in mime_type:
        from docx import Document
        doc = Document(io.BytesIO(content))
        return "\n".join(p.text for p in doc.paragraphs)
    else:
        return content.decode("utf-8", errors="ignore")

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split text into chunks of approximately chunk_size words."""
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks

# ─── Message Handlers ────────────────────────────
def handle_document_uploaded(payload: dict):
    """Process uploaded document: decrypt → extract text → chunk → embed → store vectors."""
    doc_id = payload["document_id"]
    owner_id = payload["owner_id"]
    storage_key = payload["storage_key"]
    mime_type = payload.get("mime_type", "text/plain")

    print(f"📄 Processing document: {doc_id}")
    db = get_db()
    cur = db.cursor()

    try:
        # 1. Fetch encrypted blob from MinIO
        response = minio_client.get_object(MINIO_BUCKET, storage_key)
        ciphertext = response.read()
        response.close()

        # 2. Get encryption IV from DB
        cur.execute("SELECT encryption_iv FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        if not row:
            print(f"  ✗ Document {doc_id} not found in DB")
            return
        nonce = bytes(row[0])

        # 3. Decrypt
        aesgcm = AESGCM(get_aes_key())
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)

        # 4. Extract text
        text = extract_text(plaintext, mime_type)
        if not text.strip():
            print(f"  ⚠ No text extracted from {doc_id}")
            return

        # 5. Chunk text
        chunks = chunk_text(text)
        print(f"  → {len(chunks)} chunks created")

        # 6. Store chunks in DB and generate embeddings
        points = []
        for idx, chunk_content in enumerate(chunks):
            chunk_id = str(uuid.uuid4())
            token_count = len(chunk_content.split())

            cur.execute(
                """INSERT INTO document_chunks (id, document_id, chunk_index, content, token_count, qdrant_point_id)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (chunk_id, doc_id, idx, chunk_content, token_count, chunk_id)
            )

            # Generate embedding
            vector = embedder.encode(chunk_content).tolist()
            points.append(PointStruct(
                id=chunk_id, vector=vector,
                payload={"document_id": doc_id, "owner_id": owner_id,
                         "chunk_index": idx, "content": chunk_content[:500]}
            ))

        # 7. Upsert vectors to Qdrant
        if points:
            qdrant.upsert(collection_name=COLLECTION_NAME, points=points)

        # 8. Mark document as embedded
        cur.execute("UPDATE documents SET is_embedded = true WHERE id = %s", (doc_id,))
        db.commit()
        print(f"  ✓ Document {doc_id} processed and embedded ({len(points)} vectors)")

    except Exception as e:
        db.rollback()
        print(f"  ✗ Error processing {doc_id}: {e}")
        raise
    finally:
        cur.close()
        db.close()

def handle_document_deleted(payload: dict):
    """Clean up: remove blob from MinIO, vectors from Qdrant, chunks from DB."""
    doc_id = payload["document_id"]
    storage_key = payload.get("storage_key")
    print(f"🗑 Cleaning up document: {doc_id}")

    # Remove from MinIO
    if storage_key:
        try:
            minio_client.remove_object(MINIO_BUCKET, storage_key)
        except Exception as e:
            print(f"  ⚠ MinIO cleanup failed: {e}")

    # Remove vectors from Qdrant
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qdrant.delete(collection_name=COLLECTION_NAME,
                      points_selector=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=doc_id))]))
    except Exception as e:
        print(f"  ⚠ Qdrant cleanup failed: {e}")

    # Remove chunks from DB
    db = get_db()
    cur = db.cursor()
    try:
        cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"  ⚠ DB cleanup failed: {e}")
    finally:
        cur.close()
        db.close()

    print(f"  ✓ Cleanup complete for {doc_id}")

# ─── RabbitMQ Consumer ───────────────────────────
HANDLERS = {
    "document.uploaded": handle_document_uploaded,
    "document.deleted": handle_document_deleted,
}

def on_message(channel, method, properties, body):
    try:
        message = json.loads(body)
        event_type = message.get("event_type", "")
        payload = message.get("payload", {})

        handler = HANDLERS.get(event_type)
        if handler:
            handler(payload)
            channel.basic_ack(delivery_tag=method.delivery_tag)
        else:
            print(f"⚠ Unknown event type: {event_type}")
            channel.basic_ack(delivery_tag=method.delivery_tag)

    except Exception as e:
        print(f"✗ Message processing failed: {e}")
        # Check retry count via x-death header
        deaths = (properties.headers or {}).get("x-death", []) if properties.headers else []
        retry_count = deaths[0]["count"] if deaths else 0
        if retry_count >= 3:
            print(f"  ✗ Max retries reached, sending to DLQ")
            channel.basic_reject(delivery_tag=method.delivery_tag, requeue=False)
        else:
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def main():
    print("🔧 Worker Service starting...")

    while True:
        try:
            params = pika.URLParameters(RABBITMQ_URL)
            connection = pika.BlockingConnection(params)
            channel = connection.channel()

            # Declare exchanges and queues
            channel.exchange_declare(exchange="doc.events", exchange_type="topic", durable=True)
            channel.queue_declare(queue="doc.process", durable=True, arguments={
                "x-dead-letter-exchange": "dlx.exchange",
                "x-dead-letter-routing-key": "failed",
            })
            channel.queue_declare(queue="doc.cleanup", durable=True)

            channel.queue_bind(queue="doc.process", exchange="doc.events", routing_key="document.uploaded")
            channel.queue_bind(queue="doc.cleanup", exchange="doc.events", routing_key="document.deleted")

            # DLX setup
            channel.exchange_declare(exchange="dlx.exchange", exchange_type="direct", durable=True)
            channel.queue_declare(queue="dlq.failed", durable=True)
            channel.queue_bind(queue="dlq.failed", exchange="dlx.exchange", routing_key="failed")

            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue="doc.process", on_message_callback=on_message)
            channel.basic_consume(queue="doc.cleanup", on_message_callback=on_message)

            print("✓ Connected to RabbitMQ. Waiting for messages...")
            channel.start_consuming()

        except pika.exceptions.AMQPConnectionError as e:
            print(f"⚠ RabbitMQ connection lost: {e}. Reconnecting in 5s...")
            time.sleep(5)
        except KeyboardInterrupt:
            print("Worker shutting down...")
            break

if __name__ == "__main__":
    main()
