"""
SDIP Search Service — Full-text (PostgreSQL) + Semantic (Qdrant) + Hybrid search.
"""
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from jose import jwt as jose_jwt, JWTError
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from sentence_transformers import SentenceTransformer

QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
COLLECTION_NAME = "sdip_documents"

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
qdrant: QdrantClient = None
embedder: SentenceTransformer = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_pool, qdrant, embedder
    try:
        db_pool = await asyncpg.create_pool(
            host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5432")),
            database=os.getenv("DB_NAME", "sdip_docs"), user=os.getenv("DB_USER", "doc_svc"),
            password=_read_secret("DB_PASSWORD_FILE"), min_size=2, max_size=10)
    except Exception as e:
        print(f"⚠ DB error: {e}")
    qdrant = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
    embedder = SentenceTransformer(EMBEDDING_MODEL)
    yield
    if db_pool:
        await db_pool.close()

app = FastAPI(title="SDIP Search Service", lifespan=lifespan)

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

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=20, ge=1, le=100)
    top_k: int = Field(default=10, ge=1, le=50)
    alpha: float = Field(default=0.5, ge=0.0, le=1.0, description="Weight for semantic vs fulltext (1.0=full semantic)")

@app.get("/health")
async def health():
    return {"status": "ok", "service": "search-service"}

@app.post("/search/fulltext")
async def fulltext_search(req: SearchRequest, user: dict = Depends(get_current_user)):
    offset = (req.page - 1) * req.limit
    rows = await db_pool.fetch(
        """SELECT id, title, description, file_name, created_at,
                  ts_rank(to_tsvector('english', title || ' ' || description), plainto_tsquery('english', $1)) AS rank
           FROM documents
           WHERE owner_id = $2 AND is_deleted = false
             AND to_tsvector('english', title || ' ' || description) @@ plainto_tsquery('english', $1)
           ORDER BY rank DESC LIMIT $3 OFFSET $4""",
        req.query, uuid.UUID(user["sub"]), req.limit, offset)
    total = await db_pool.fetchval(
        """SELECT COUNT(*) FROM documents
           WHERE owner_id = $1 AND is_deleted = false
             AND to_tsvector('english', title || ' ' || description) @@ plainto_tsquery('english', $2)""",
        uuid.UUID(user["sub"]), req.query)
    return {"results": [dict(r) for r in rows], "total": total, "page": req.page}

@app.post("/search/semantic")
async def semantic_search(req: SearchRequest, user: dict = Depends(get_current_user)):
    query_vector = embedder.encode(req.query).tolist()
    results = qdrant.search(
        collection_name=COLLECTION_NAME, query_vector=query_vector,
        query_filter=Filter(must=[FieldCondition(key="owner_id", match=MatchValue(value=user["sub"]))]),
        limit=req.top_k)
    items = [{"document_id": h.payload.get("document_id"), "chunk_index": h.payload.get("chunk_index"),
              "score": round(h.score, 4), "snippet": h.payload.get("content", "")[:300]} for h in results]
    return {"results": items, "total": len(items)}

@app.post("/search/hybrid")
async def hybrid_search(req: SearchRequest, user: dict = Depends(get_current_user)):
    """Reciprocal Rank Fusion (RRF) of fulltext + semantic results."""
    ft_res = await fulltext_search(req, user)
    sem_res = await semantic_search(req, user)

    rrf_scores = {}
    k = 60  # RRF constant
    for rank, item in enumerate(ft_res["results"]):
        doc_id = str(item["id"])
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + (1 - req.alpha) / (k + rank + 1)
        rrf_scores[f"_data_{doc_id}"] = item
    for rank, item in enumerate(sem_res["results"]):
        doc_id = item["document_id"]
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + req.alpha / (k + rank + 1)
        if f"_data_{doc_id}" not in rrf_scores:
            rrf_scores[f"_data_{doc_id}"] = item

    scored = [(did, score) for did, score in rrf_scores.items() if not did.startswith("_data_")]
    scored.sort(key=lambda x: x[1], reverse=True)

    fused = []
    for doc_id, score in scored[:req.top_k]:
        data = rrf_scores.get(f"_data_{doc_id}", {})
        fused.append({**data, "rrf_score": round(score, 6)})

    return {"results": fused, "total": len(fused), "method": "hybrid_rrf"}
