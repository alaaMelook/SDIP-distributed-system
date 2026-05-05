# Secure Document Intelligence Platform (SDIP)
## Complete System Design — University Final Project

---

# Section 1: System Overview

## 1.1 Project Choice: AI-Based — Secure Document Intelligence Platform

**Justification:** A Secure Document Intelligence Platform is chosen because it naturally demands every pillar of a secure distributed architecture:

| Concern | Why SDIP Needs It |
|---|---|
| **Distributed services** | Upload, OCR/AI processing, search, and audit are independent bounded contexts |
| **Strong authentication** | Documents are sensitive — multi-factor, OAuth, JWT are mandatory |
| **RBAC** | Admins manage users/policies; regular users only access their own documents |
| **Encryption at rest** | Uploaded files contain PII/financial data |
| **Async processing** | AI inference (RAG, embedding generation) is CPU-heavy — must be offloaded to workers |
| **Message queue** | Decouples upload from processing; enables retry and dead-letter handling |
| **Vector database** | Semantic search over document content requires vector embeddings |
| **Audit logging** | Regulatory compliance requires full traceability of who accessed what |

## 1.2 Core Capabilities

1. **Secure File Upload** — Encrypted storage with integrity verification (SHA-256)
2. **AI-Powered Document Analysis** — RAG pipeline: embed → store → query with LLM
3. **Semantic Search** — Natural-language queries over uploaded documents
4. **Role-Based Access Control** — Admin vs. User with fine-grained permissions
5. **OAuth Integration** — Google, GitHub, Microsoft SSO
6. **Full Audit Trail** — Every action logged with tamper-evident checksums
7. **Attack Resilience** — Rate limiting, input validation, file-type restrictions

## 1.3 Technology Stack

| Layer | Technology |
|---|---|
| API Gateway | Nginx (HTTPS, rate limiting, reverse proxy) |
| Auth Service | Node.js + Express |
| Document Service | Python (FastAPI) |
| AI Service | Python (FastAPI + LangChain) |
| Search Service | Python (FastAPI) |
| Worker Service | Python (Celery-style consumer) |
| Message Queue | RabbitMQ 3.13 |
| Primary Database | PostgreSQL 16 |
| Vector Database | Qdrant |
| Object Storage | MinIO (S3-compatible) |
| Logging/Audit | Python (FastAPI) + PostgreSQL |
| Containerization | Docker + Docker Compose |

---

# Section 2: Architecture Diagram

## 2.1 High-Level Architecture (Text Description)

```
┌─────────────────────────────────────────────────────────────────────┐
│                         EXTERNAL CLIENTS                            │
│              (Browser / Mobile / API Consumers)                     │
└──────────────────────────┬──────────────────────────────────────────┘
                           │ HTTPS (TLS 1.3)
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     NGINX API GATEWAY (:443)                        │
│  ┌─────────────┐ ┌──────────────┐ ┌────────────┐ ┌──────────────┐  │
│  │Rate Limiter │ │ SSL Termination│ │CORS Policy │ │Request Router│  │
│  └─────────────┘ └──────────────┘ └────────────┘ └──────┬───────┘  │
└─────────────────────────────────────────────────────────┬───────────┘
          │               │               │               │
          ▼               ▼               ▼               ▼
   ┌────────────┐  ┌────────────┐  ┌────────────┐  ┌────────────┐
   │   Auth     │  │  Document  │  │    AI      │  │  Search    │
   │  Service   │  │  Service   │  │  Service   │  │  Service   │
   │  (:3001)   │  │  (:3002)   │  │  (:3003)   │  │  (:3004)   │
   └─────┬──────┘  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘
         │               │               │               │
         │         ┌─────▼──────┐         │               │
         │         │   MinIO    │         │               │
         │         │  (Storage) │         │               │
         │         │  (:9000)   │         │               │
         │         └────────────┘         │               │
         │               │               │               │
         ▼               ▼               ▼               ▼
   ┌──────────────────────────────────────────────────────────┐
   │                    RabbitMQ (:5672)                       │
   │  Exchanges: doc.events | ai.tasks | audit.events         │
   └────────────────────────┬─────────────────────────────────┘
                            │
                ┌───────────┼───────────┐
                ▼                       ▼
        ┌──────────────┐       ┌──────────────┐
        │    Worker     │       │  Audit/Log   │
        │   Service    │       │   Service    │
        │   (:3005)    │       │   (:3006)    │
        └───────┬──────┘       └───────┬──────┘
                │                      │
                ▼                      ▼
   ┌─────────────────┐    ┌─────────────────┐
   │   PostgreSQL    │    │     Qdrant      │
   │    (:5432)      │    │    (:6333)      │
   └─────────────────┘    └─────────────────┘
```

## 2.2 Network Topology

- **External network:** Only Nginx port 443 exposed
- **Internal network (`sdip-internal`):** All services communicate over Docker bridge
- **Database network (`sdip-data`):** Only services needing DB access are attached
- All inter-service calls use **mTLS** or **shared JWT verification**

---

# Section 3: Microservices Breakdown

## 3.1 Auth Service (Node.js + Express) — Port 3001

**Responsibility:** User registration, login, JWT issuance/refresh, OAuth flows, RBAC enforcement.

| Feature | Implementation |
|---|---|
| Password hashing | bcrypt (cost factor 12) |
| Token format | JWT (RS256, 15-min access, 7-day refresh) |
| OAuth providers | Google, GitHub, Microsoft via Passport.js |
| RBAC | Middleware extracts role from JWT; `admin` / `user` enum |
| Session management | Refresh tokens stored in DB with device fingerprint |
| Brute-force protection | Account lockout after 5 failed attempts (15-min cooldown) |

**Key Endpoints:**
```
POST   /auth/register          — Create account (bcrypt hash)
POST   /auth/login             — Issue JWT pair
POST   /auth/refresh           — Rotate refresh token
POST   /auth/logout            — Revoke refresh token
GET    /auth/oauth/google      — Initiate Google OAuth
GET    /auth/oauth/github      — Initiate GitHub OAuth
GET    /auth/oauth/microsoft   — Initiate Microsoft OAuth
GET    /auth/oauth/callback    — OAuth callback handler
GET    /auth/me                — Get current user profile
```

---

## 3.2 Document Service (Python FastAPI) — Port 3002

**Responsibility:** File upload, download, metadata management, encryption at rest, integrity verification.

| Feature | Implementation |
|---|---|
| Upload validation | Allowlist: `.pdf`, `.docx`, `.txt`, `.md` (max 50 MB) |
| Encryption at rest | AES-256-GCM via `cryptography` library; key from env secret |
| Integrity | SHA-256 hash computed on upload, verified on download |
| Storage | MinIO (S3-compatible), encrypted blobs |
| Access control | Owner-only by default; admins can access all |

**Key Endpoints:**
```
POST   /documents/upload       — Upload + encrypt + hash (auth required)
GET    /documents/{id}         — Retrieve metadata (auth required, owner/admin)
GET    /documents/{id}/download — Decrypt + stream + verify integrity
DELETE /documents/{id}         — Soft-delete (owner/admin)
GET    /documents/             — List user's documents (paginated)
GET    /documents/admin/all    — List all documents (admin only)
```

**Upload Flow:**
1. Validate file type (magic bytes + extension) and size
2. Compute SHA-256 hash of plaintext
3. Encrypt file with AES-256-GCM (unique IV per file)
4. Upload encrypted blob to MinIO
5. Store metadata (hash, size, MIME, owner, encryption IV) in PostgreSQL
6. Publish `document.uploaded` event to RabbitMQ

---

## 3.3 AI Service (Python FastAPI + LangChain) — Port 3003

**Responsibility:** Document embedding, RAG-based question answering, AI inference.

| Feature | Implementation |
|---|---|
| Embedding model | `all-MiniLM-L6-v2` via Sentence Transformers (384-dim) |
| LLM | Ollama (local) or OpenAI API (configurable) |
| RAG framework | LangChain with Qdrant retriever |
| Vector DB | Qdrant (cosine similarity, HNSW index) |

**Key Endpoints:**
```
POST   /ai/embed/{doc_id}     — Trigger embedding generation (auth required)
POST   /ai/query              — Ask a question over user's documents (RAG)
GET    /ai/status/{task_id}   — Check async task status
DELETE /ai/vectors/{doc_id}   — Remove embeddings when document deleted
```

**RAG Pipeline:**
1. User submits question → AI Service receives query
2. Generate query embedding using same model
3. Search Qdrant for top-k similar chunks (filtered by user's document IDs)
4. Construct prompt: system instructions + retrieved chunks + user question
5. Send to LLM → return generated answer with source references

---

## 3.4 Search Service (Python FastAPI) — Port 3004

**Responsibility:** Full-text search (PostgreSQL `tsvector`) and semantic search (Qdrant) with unified API.

**Key Endpoints:**
```
POST   /search/fulltext       — PostgreSQL full-text search
POST   /search/semantic       — Qdrant vector similarity search
POST   /search/hybrid         — Combined ranking (RRF fusion)
```

---

## 3.5 Worker Service (Python) — Port 3005

**Responsibility:** Consume RabbitMQ messages, perform async tasks (embedding generation, file processing, cleanup).

| Queue | Task |
|---|---|
| `doc.process` | Extract text from uploaded document (OCR/parsing) |
| `ai.embed` | Generate embeddings and store in Qdrant |
| `doc.cleanup` | Remove encrypted blobs + vectors on document deletion |

**Implementation:** Uses `pika` (RabbitMQ client) with prefetch=1, manual acknowledgment, dead-letter exchange for failed tasks (max 3 retries).

---

## 3.6 Audit/Logging Service (Python FastAPI) — Port 3006

**Responsibility:** Centralized logging, audit trail, security event monitoring.

**Consumes events from RabbitMQ `audit.events` exchange:**

| Event Type | Examples |
|---|---|
| `auth.*` | Login success/failure, token refresh, OAuth login, account lockout |
| `document.*` | Upload, download, delete, share |
| `admin.*` | User role change, system config update, bulk operations |
| `security.*` | Rate limit hit, invalid token, forbidden access attempt, file validation failure |

**Key Endpoints:**
```
GET    /audit/logs             — Query audit logs (admin only, paginated)
GET    /audit/logs/user/{id}   — Get logs for specific user (admin only)
GET    /audit/security-events  — Security violation dashboard (admin only)
GET    /audit/stats            — Aggregate statistics (admin only)
```

---

# Section 4: Security Architecture

## 4.1 Authentication

### JWT Implementation
```
Access Token:
{
  "sub": "user-uuid",
  "role": "user|admin",
  "iat": 1700000000,
  "exp": 1700000900,       // 15 minutes
  "iss": "sdip-auth",
  "aud": "sdip-services"
}
Signed with RS256 (RSA 2048-bit private key)
Verified by all services using the public key
```

### Password Security
- **Hashing:** bcrypt with cost factor 12 (~250ms per hash)
- **Requirements:** Min 8 chars, 1 uppercase, 1 lowercase, 1 digit, 1 special
- **Storage:** Only bcrypt hash stored; plaintext never logged

### OAuth 2.0 Flow (PKCE)
1. Client redirects to provider with `code_challenge` (S256)
2. Provider authenticates user, redirects back with `authorization_code`
3. Auth Service exchanges code + `code_verifier` for provider tokens
4. Auth Service creates/links local user account
5. Issues SDIP JWT pair to client

## 4.2 Authorization (RBAC)

| Role | Permissions |
|---|---|
| `user` | CRUD own documents, query AI on own docs, view own audit logs |
| `admin` | All user permissions + manage users, view all docs, view all audit logs, system config |

**Enforcement:** Middleware on every route checks `req.user.role` from decoded JWT.

```python
# FastAPI dependency example
def require_role(allowed: list[str]):
    def checker(user = Depends(get_current_user)):
        if user.role not in allowed:
            raise HTTPException(403, "Insufficient permissions")
        return user
    return checker

@app.get("/admin/users", dependencies=[Depends(require_role(["admin"]))])
async def list_users(): ...
```

## 4.3 HTTPS Enforcement

**Nginx Configuration (key excerpt):**
```nginx
server {
    listen 80;
    server_name sdip.local;
    return 301 https://$host$request_uri;   # Force HTTPS redirect
}

server {
    listen 443 ssl http2;
    server_name sdip.local;

    ssl_certificate     /etc/nginx/ssl/sdip.crt;
    ssl_certificate_key /etc/nginx/ssl/sdip.key;
    ssl_protocols       TLSv1.3;
    ssl_ciphers         TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256;
    ssl_prefer_server_ciphers on;

    # HSTS Header
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header X-XSS-Protection "1; mode=block" always;
}
```

## 4.4 Rate Limiting

**Nginx rate limiting:**
```nginx
# Global: 10 requests/second per IP
limit_req_zone $binary_remote_addr zone=global:10m rate=10r/s;

# Auth endpoints: 5 requests/minute per IP (brute-force protection)
limit_req_zone $binary_remote_addr zone=auth:10m rate=5r/m;

location /api/auth/ {
    limit_req zone=auth burst=3 nodelay;
    limit_req_status 429;
    proxy_pass http://auth-service:3001;
}

location /api/ {
    limit_req zone=global burst=20 nodelay;
    limit_req_status 429;
    proxy_pass http://upstream;
}
```

## 4.5 Input Validation

**Every service validates all inputs:**

```python
# Pydantic model example (Document Service)
class DocumentUpload(BaseModel):
    title: str = Field(..., min_length=1, max_length=255, pattern=r'^[\w\s\-\.]+$')
    description: str = Field(default="", max_length=2000)
    tags: list[str] = Field(default=[], max_length=10)

    @field_validator('tags')
    def validate_tags(cls, v):
        for tag in v:
            if len(tag) > 50 or not re.match(r'^[\w\-]+$', tag):
                raise ValueError(f'Invalid tag: {tag}')
        return v
```

**File upload validation (defense in depth):**
1. **Extension check:** Allowlist (`.pdf`, `.docx`, `.txt`, `.md`)
2. **Magic bytes:** Verify file signature matches claimed type
3. **Size limit:** Max 50 MB (enforced at Nginx + application level)
4. **Filename sanitization:** Strip path traversal, special characters
5. **Antivirus scan:** ClamAV integration (optional, via worker)

## 4.6 File Encryption at Rest

```python
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import os

def encrypt_file(plaintext: bytes, key: bytes) -> tuple[bytes, bytes]:
    """Encrypt with AES-256-GCM. Returns (nonce, ciphertext)."""
    nonce = os.urandom(12)  # 96-bit nonce
    aesgcm = AESGCM(key)   # key = 32 bytes from env secret
    ciphertext = aesgcm.encrypt(nonce, plaintext, None)
    return nonce, ciphertext

def decrypt_file(nonce: bytes, ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt and verify integrity (GCM provides authentication)."""
    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, None)  # Raises on tampering
```

## 4.7 Integrity Verification (SHA-256)

```python
import hashlib

def compute_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

# On upload: store hash in DB
# On download: recompute and compare
def verify_integrity(data: bytes, expected_hash: str) -> bool:
    return hashlib.sha256(data).hexdigest() == expected_hash
```

## 4.8 Service-to-Service Security

- **Internal JWT:** Services call each other with a short-lived service JWT (`iss: sdip-internal`, `sub: service-name`)
- **Network isolation:** Docker networks restrict which services can communicate
- **No external exposure:** Only Nginx port 443 is published to the host

## 4.9 Secrets Management

```yaml
# .env file (NEVER committed to Git)
POSTGRES_PASSWORD=<generated-64-char-random>
JWT_PRIVATE_KEY_PATH=/run/secrets/jwt_private_key
JWT_PUBLIC_KEY_PATH=/run/secrets/jwt_public_key
AES_ENCRYPTION_KEY=<32-byte-hex-encoded>
RABBITMQ_PASSWORD=<generated-64-char-random>
MINIO_SECRET_KEY=<generated-64-char-random>
OAUTH_GOOGLE_CLIENT_SECRET=<from-google-console>
OAUTH_GITHUB_CLIENT_SECRET=<from-github-settings>
OAUTH_MICROSOFT_CLIENT_SECRET=<from-azure-portal>
```

**Docker Secrets (production):**
```yaml
secrets:
  jwt_private_key:
    file: ./secrets/jwt_private.pem
  jwt_public_key:
    file: ./secrets/jwt_public.pem
  db_password:
    file: ./secrets/db_password.txt
```

## 4.10 Database Security

- **Principle of least privilege:** Each service has its own DB user with minimal permissions
- **Parameterized queries:** All SQL uses parameterized statements (SQLAlchemy ORM)
- **Connection encryption:** `sslmode=require` for all PostgreSQL connections
- **No default credentials:** All passwords generated at deployment time
- **Backup encryption:** Database backups encrypted with GPG

---

# Section 5: Database Design

## 5.1 PostgreSQL Schema

### Users Table
```sql
CREATE TABLE users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) UNIQUE NOT NULL,
    password_hash   VARCHAR(255),          -- NULL for OAuth-only users
    role            VARCHAR(20) NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
    display_name    VARCHAR(255) NOT NULL,
    avatar_url      VARCHAR(500),
    oauth_provider  VARCHAR(50),           -- 'google', 'github', 'microsoft', NULL
    oauth_id        VARCHAR(255),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    failed_login_attempts INT NOT NULL DEFAULT 0,
    locked_until    TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE(oauth_provider, oauth_id)
);
CREATE INDEX idx_users_email ON users(email);
```

### Refresh Tokens Table
```sql
CREATE TABLE refresh_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash      VARCHAR(255) NOT NULL,  -- SHA-256 of the refresh token
    device_info     VARCHAR(500),
    expires_at      TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    revoked_at      TIMESTAMP WITH TIME ZONE
);
CREATE INDEX idx_refresh_tokens_user ON refresh_tokens(user_id);
```

### Documents Table
```sql
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title           VARCHAR(255) NOT NULL,
    description     TEXT DEFAULT '',
    file_name       VARCHAR(255) NOT NULL,     -- Sanitized original filename
    file_size       BIGINT NOT NULL,
    mime_type       VARCHAR(100) NOT NULL,
    storage_key     VARCHAR(500) NOT NULL,      -- MinIO object key (encrypted blob)
    encryption_iv   BYTEA NOT NULL,             -- AES-GCM nonce (12 bytes)
    sha256_hash     VARCHAR(64) NOT NULL,       -- Hash of plaintext for integrity
    is_embedded     BOOLEAN NOT NULL DEFAULT false,
    is_deleted      BOOLEAN NOT NULL DEFAULT false,
    deleted_at      TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_documents_owner ON documents(owner_id);
CREATE INDEX idx_documents_fulltext ON documents USING GIN(to_tsvector('english', title || ' ' || description));
```

### Document Chunks Table (for RAG)
```sql
CREATE TABLE document_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INT NOT NULL,
    content         TEXT NOT NULL,
    token_count     INT NOT NULL,
    qdrant_point_id UUID NOT NULL,              -- Reference to vector in Qdrant
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_chunks_document ON document_chunks(document_id);
```

### Audit Logs Table
```sql
CREATE TABLE audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp       TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    user_id         UUID REFERENCES users(id),  -- NULL for system events
    action          VARCHAR(100) NOT NULL,       -- e.g., 'auth.login', 'document.upload'
    resource_type   VARCHAR(50),                 -- e.g., 'document', 'user'
    resource_id     UUID,
    ip_address      INET,
    user_agent      VARCHAR(500),
    details         JSONB DEFAULT '{}',          -- Additional structured data
    severity        VARCHAR(20) NOT NULL DEFAULT 'info'
                    CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    checksum        VARCHAR(64) NOT NULL         -- SHA-256 for tamper detection
);
CREATE INDEX idx_audit_timestamp ON audit_logs(timestamp DESC);
CREATE INDEX idx_audit_user ON audit_logs(user_id);
CREATE INDEX idx_audit_action ON audit_logs(action);
CREATE INDEX idx_audit_severity ON audit_logs(severity);

-- Partition by month for performance
-- CREATE TABLE audit_logs_2026_05 PARTITION OF audit_logs
--   FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
```

### Tags Table
```sql
CREATE TABLE tags (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(50) UNIQUE NOT NULL
);

CREATE TABLE document_tags (
    document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag_id          UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (document_id, tag_id)
);
```

## 5.2 ER Diagram

```
┌──────────────┐     1:N     ┌─────────────────┐
│    users     │────────────▶│ refresh_tokens   │
└──────┬───────┘             └─────────────────┘
       │ 1:N
       ▼
┌──────────────┐     1:N     ┌─────────────────┐     N:1     ┌──────────┐
│  documents   │────────────▶│ document_chunks  │────────────▶│  qdrant  │
└──────┬───────┘             └─────────────────┘             │ (vectors)│
       │ N:M                                                  └──────────┘
       ▼
┌──────────────┐
│document_tags │──▶ tags
└──────────────┘

┌──────────────┐
│  audit_logs  │ (references users.id, standalone)
└──────────────┘
```

## 5.3 Database Users (Least Privilege)

```sql
-- Auth Service: only users + refresh_tokens
CREATE ROLE auth_svc LOGIN PASSWORD '<secret>';
GRANT SELECT, INSERT, UPDATE ON users, refresh_tokens TO auth_svc;

-- Document Service: documents + tags
CREATE ROLE doc_svc LOGIN PASSWORD '<secret>';
GRANT SELECT, INSERT, UPDATE ON documents, document_chunks, tags, document_tags TO doc_svc;
GRANT SELECT ON users TO doc_svc;  -- For ownership verification

-- Audit Service: audit_logs only
CREATE ROLE audit_svc LOGIN PASSWORD '<secret>';
GRANT SELECT, INSERT ON audit_logs TO audit_svc;

-- Admin read-only for reporting
CREATE ROLE admin_readonly LOGIN PASSWORD '<secret>';
GRANT SELECT ON ALL TABLES IN SCHEMA public TO admin_readonly;
```
