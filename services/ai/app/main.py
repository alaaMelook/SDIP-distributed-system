"""
SDIP AI Service — RAG pipeline, embeddings, and LLM inference.
"""
import os
import uuid
import json
import hashlib
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import httpx
from fastapi import FastAPI, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from jose import jwt as jose_jwt, JWTError
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer

# ─── Config ──────────────────────────────────────
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
COLLECTION_NAME = "sdip_documents"
VECTOR_DIM = 384  # all-MiniLM-L6-v2 dimension

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

# ─── Globals ─────────────────────────────────────
db_pool = None
qdrant: QdrantClient = None
embedder: SentenceTransformer = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, qdrant, embedder

    # Database
    try:
        db_pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "sdip_docs"),
            user=os.getenv("DB_USER", "doc_svc"),
            password=_read_secret("DB_PASSWORD_FILE"),
            min_size=2, max_size=10,
        )
    except Exception as e:
        print(f"⚠ DB connection failed: {e}")

    # Qdrant
    qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    try:
        qdrant.get_collection(COLLECTION_NAME)
    except Exception:
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=VECTOR_DIM, distance=Distance.COSINE),
        )

    # Embedding model
    print(f"Loading embedding model: {EMBEDDING_MODEL}...")
    embedder = SentenceTransformer(EMBEDDING_MODEL)
    print("✓ Model loaded")

    yield
    if db_pool:
        await db_pool.close()

app = FastAPI(title="SDIP AI Service", lifespan=lifespan)

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

# ─── Models ──────────────────────────────────────
class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    doc_ids: list[str] = Field(default=[], description="Filter to specific document IDs")
    top_k: int = Field(default=5, ge=1, le=20)

class QueryResponse(BaseModel):
    answer: str
    sources: list[dict]

# ─── LLM Call ────────────────────────────────────
async def call_llm(prompt: str) -> str:
    if LLM_PROVIDER == "ollama":
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(f"{OLLAMA_HOST}/api/generate", json={
                "model": "llama3.2", "prompt": prompt, "stream": False,
            })
            if resp.status_code == 200:
                return resp.json().get("response", "No response from LLM")
            return f"LLM error: {resp.status_code}"
    elif LLM_PROVIDER == "openai":
        api_key = os.getenv("OPENAI_API_KEY", "")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": prompt}], "max_tokens": 1000})
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            return f"OpenAI error: {resp.status_code}"
    return "LLM provider not configured"

# ─── Routes ──────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "ai-service", "model": EMBEDDING_MODEL}

@app.post("/ai/embed/{doc_id}")
async def embed_document(doc_id: str, user: dict = Depends(get_current_user)):
    """Generate embeddings for a document's chunks and store in Qdrant."""
    # Verify ownership
    doc = await db_pool.fetchrow(
        "SELECT owner_id FROM documents WHERE id = $1 AND is_deleted = false", uuid.UUID(doc_id))
    if not doc:
        raise HTTPException(404, "Document not found")
    if str(doc["owner_id"]) != user["sub"] and user.get("role") != "admin":
        raise HTTPException(403, "Access denied")

    # Get chunks
    chunks = await db_pool.fetch(
        "SELECT id, chunk_index, content FROM document_chunks WHERE document_id = $1 ORDER BY chunk_index",
        uuid.UUID(doc_id))
    if not chunks:
        raise HTTPException(404, "No chunks found. Document may not be processed yet.")

    # Generate embeddings
    texts = [c["content"] for c in chunks]
    vectors = embedder.encode(texts).tolist()

    # Upsert to Qdrant
    points = []
    for chunk, vector in zip(chunks, vectors):
        point_id = str(chunk["id"])
        points.append(PointStruct(
            id=point_id,
            vector=vector,
            payload={"document_id": doc_id, "owner_id": user["sub"],
                     "chunk_index": chunk["chunk_index"], "content": chunk["content"][:500]},
        ))
        await db_pool.execute(
            "UPDATE document_chunks SET qdrant_point_id = $1 WHERE id = $2",
            uuid.UUID(point_id), chunk["id"])

    qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
    await db_pool.execute("UPDATE documents SET is_embedded = true WHERE id = $1", uuid.UUID(doc_id))

    return {"status": "completed", "chunks_embedded": len(points), "document_id": doc_id}

@app.post("/ai/query", response_model=QueryResponse)
async def query_documents(req: QueryRequest, user: dict = Depends(get_current_user)):
    """RAG query: embed question → search Qdrant → generate answer with LLM."""
    query_vector = embedder.encode(req.question).tolist()

    # Build filter for user's documents
    filter_conditions = [FieldCondition(key="owner_id", match=MatchValue(value=user["sub"]))]
    if req.doc_ids:
        filter_conditions = [FieldCondition(key="document_id", match=MatchValue(value=did)) for did in req.doc_ids]

    # Search Qdrant
    results = qdrant.search(
        collection_name=COLLECTION_NAME,
        query_vector=query_vector,
        query_filter=Filter(should=filter_conditions) if req.doc_ids else Filter(must=filter_conditions),
        limit=req.top_k,
    )

    if not results:
        return QueryResponse(answer="No relevant documents found for your query.", sources=[])

    # Build RAG prompt
    context_chunks = []
    sources = []
    for hit in results:
        content = hit.payload.get("content", "")
        context_chunks.append(content)
        sources.append({
            "document_id": hit.payload.get("document_id"),
            "chunk_index": hit.payload.get("chunk_index"),
            "score": round(hit.score, 4),
            "snippet": content[:200],
        })

    context = "\n\n---\n\n".join(context_chunks)
    prompt = f"""You are an AI assistant for a document intelligence platform.
Answer the user's question based ONLY on the provided document excerpts.
If the answer is not in the documents, say so clearly.

DOCUMENT EXCERPTS:
{context}

USER QUESTION: {req.question}

ANSWER:"""

    answer = await call_llm(prompt)
    return QueryResponse(answer=answer, sources=sources)

@app.delete("/ai/vectors/{doc_id}")
async def delete_vectors(doc_id: str, user: dict = Depends(get_current_user)):
    """Remove all vectors for a document from Qdrant."""
    doc = await db_pool.fetchrow(
        "SELECT owner_id FROM documents WHERE id = $1", uuid.UUID(doc_id))
    if not doc:
        raise HTTPException(404, "Document not found")
    if str(doc["owner_id"]) != user["sub"] and user.get("role") != "admin":
        raise HTTPException(403, "Access denied")

    # Delete by filter
    qdrant.delete(
        collection_name=COLLECTION_NAME,
        points_selector=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=doc_id))]),
    )
    await db_pool.execute("UPDATE documents SET is_embedded = false WHERE id = $1", uuid.UUID(doc_id))
    return {"deleted": True, "document_id": doc_id}
