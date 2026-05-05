# Sections 6–10: SDIP Design (Continued)

---

# Section 6: Message Queue Flow

## 6.1 RabbitMQ Topology

| Exchange | Type | Bound Queues | Purpose |
|---|---|---|---|
| `doc.events` | topic | `doc.process`, `doc.cleanup` | Document lifecycle events |
| `ai.tasks` | direct | `ai.embed`, `ai.delete` | AI processing tasks |
| `audit.events` | fanout | `audit.log` | All audit events broadcast |

## 6.2 Async Workflows

### Document Upload Flow
```
Document Service                RabbitMQ                 Worker Service
     │                             │                          │
     │── publish ─────────────────▶│                          │
     │   routing_key:              │                          │
     │   "document.uploaded"       │                          │
     │   payload: {doc_id,         │── deliver ──────────────▶│
     │     owner_id, storage_key}  │   queue: doc.process     │
     │                             │                          │
     │                             │                          │── 1. Fetch encrypted blob from MinIO
     │                             │                          │── 2. Decrypt file
     │                             │                          │── 3. Extract text (PyPDF2/docx)
     │                             │                          │── 4. Chunk text (512 tokens)
     │                             │                          │── 5. Store chunks in PostgreSQL
     │                             │                          │
     │                             │◀── publish ──────────────│
     │                             │   routing_key:           │
     │                             │   "document.processed"   │
     │                             │                          │
     │                             │── deliver ──────────────▶│ (self-consume or AI worker)
     │                             │   queue: ai.embed        │
     │                             │                          │── 6. Generate embeddings
     │                             │                          │── 7. Upsert to Qdrant
     │                             │                          │── 8. Update documents.is_embedded=true
     │                             │                          │
     │                             │◀── publish (audit) ──────│
     │                             │   exchange: audit.events │
```

### Document Deletion Flow
```
Document Service ──▶ RabbitMQ (doc.cleanup) ──▶ Worker:
  1. Delete encrypted blob from MinIO
  2. Delete vectors from Qdrant
  3. Delete chunks from PostgreSQL
  4. Publish audit event
```

### Dead Letter Handling
```yaml
# Failed messages after 3 retries go to DLX
x-dead-letter-exchange: dlx.exchange
x-dead-letter-routing-key: failed
x-message-ttl: 300000   # 5 min retry delay
Max retries: 3 (tracked via x-death header)
```

## 6.3 Message Schema Example

```json
{
  "event_id": "uuid-v4",
  "event_type": "document.uploaded",
  "timestamp": "2026-05-05T00:00:00Z",
  "payload": {
    "document_id": "uuid",
    "owner_id": "uuid",
    "storage_key": "documents/uuid/encrypted.bin",
    "file_name": "report.pdf",
    "mime_type": "application/pdf"
  },
  "metadata": {
    "source_service": "document-service",
    "correlation_id": "uuid",
    "retry_count": 0
  }
}
```

---

# Section 7: API Specification

## 7.1 Auth Service APIs

| Method | Endpoint | Auth | Role | Input | Output |
|---|---|---|---|---|---|
| POST | `/auth/register` | None | Any | `{email, password, display_name}` | `{user, access_token, refresh_token}` |
| POST | `/auth/login` | None | Any | `{email, password}` | `{access_token, refresh_token}` |
| POST | `/auth/refresh` | Refresh Token | Any | `{refresh_token}` | `{access_token, refresh_token}` |
| POST | `/auth/logout` | JWT | Any | `{refresh_token}` | `{message: "logged out"}` |
| GET | `/auth/me` | JWT | Any | — | `{user profile}` |
| GET | `/auth/oauth/:provider` | None | Any | — | Redirect to OAuth provider |
| PUT | `/auth/users/:id/role` | JWT | Admin | `{role: "admin"\|"user"}` | `{updated user}` |
| GET | `/auth/users` | JWT | Admin | `?page,limit` | `{users[], total}` |

## 7.2 Document Service APIs

| Method | Endpoint | Auth | Role | Input | Output |
|---|---|---|---|---|---|
| POST | `/documents/upload` | JWT | Any | Multipart form (file + metadata) | `{document metadata, sha256}` |
| GET | `/documents/` | JWT | Any | `?page,limit,search` | `{documents[], total}` |
| GET | `/documents/:id` | JWT | Owner/Admin | — | `{document metadata}` |
| GET | `/documents/:id/download` | JWT | Owner/Admin | — | File stream (decrypted) |
| DELETE | `/documents/:id` | JWT | Owner/Admin | — | `{message: "deleted"}` |
| GET | `/documents/admin/all` | JWT | Admin | `?page,limit` | `{all documents[], total}` |

## 7.3 AI Service APIs

| Method | Endpoint | Auth | Role | Input | Output |
|---|---|---|---|---|---|
| POST | `/ai/query` | JWT | Any | `{question, doc_ids[]}` | `{answer, sources[]}` |
| POST | `/ai/embed/:doc_id` | JWT | Owner/Admin | — | `{task_id, status}` |
| GET | `/ai/status/:task_id` | JWT | Any | — | `{status, progress}` |
| DELETE | `/ai/vectors/:doc_id` | JWT | Owner/Admin | — | `{deleted_count}` |

## 7.4 Search Service APIs

| Method | Endpoint | Auth | Role | Input | Output |
|---|---|---|---|---|---|
| POST | `/search/fulltext` | JWT | Any | `{query, page, limit}` | `{results[], total}` |
| POST | `/search/semantic` | JWT | Any | `{query, top_k}` | `{results[] with scores}` |
| POST | `/search/hybrid` | JWT | Any | `{query, top_k, alpha}` | `{fused results[]}` |

## 7.5 Audit Service APIs

| Method | Endpoint | Auth | Role | Input | Output |
|---|---|---|---|---|---|
| GET | `/audit/logs` | JWT | Admin | `?action,severity,from,to,page` | `{logs[], total}` |
| GET | `/audit/logs/user/:id` | JWT | Admin | `?from,to` | `{logs[]}` |
| GET | `/audit/security-events` | JWT | Admin | `?severity,from,to` | `{events[]}` |
| GET | `/audit/stats` | JWT | Admin | `?period` | `{counts by action/severity}` |

## 7.6 Standard Error Response

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Human-readable description",
    "details": [{"field": "email", "issue": "Invalid format"}],
    "request_id": "correlation-uuid"
  }
}
```

---

# Section 8: Docker Compose Design

## 8.1 Complete `docker-compose.yml`

```yaml
version: "3.9"

x-common-env: &common-env
  NODE_ENV: production
  JWT_PUBLIC_KEY_PATH: /run/secrets/jwt_public_key

services:
  # ─── API GATEWAY ──────────────────────────────
  nginx:
    image: nginx:1.27-alpine
    ports:
      - "443:443"
      - "80:80"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/ssl:/etc/nginx/ssl:ro
    depends_on:
      - auth-service
      - document-service
      - ai-service
      - search-service
    networks:
      - sdip-external
      - sdip-internal
    restart: unless-stopped

  # ─── AUTH SERVICE ─────────────────────────────
  auth-service:
    build: ./services/auth
    environment:
      <<: *common-env
      DB_HOST: postgres
      DB_NAME: sdip_auth
      DB_USER: auth_svc
      DB_PASSWORD_FILE: /run/secrets/db_auth_password
      RABBITMQ_URL: amqp://sdip:${RABBITMQ_PASSWORD}@rabbitmq:5672
      OAUTH_GOOGLE_CLIENT_ID: ${OAUTH_GOOGLE_CLIENT_ID}
      OAUTH_GOOGLE_CLIENT_SECRET_FILE: /run/secrets/oauth_google
      OAUTH_GITHUB_CLIENT_ID: ${OAUTH_GITHUB_CLIENT_ID}
      OAUTH_GITHUB_CLIENT_SECRET_FILE: /run/secrets/oauth_github
      BCRYPT_ROUNDS: 12
    secrets:
      - jwt_private_key
      - jwt_public_key
      - db_auth_password
      - oauth_google
      - oauth_github
    networks:
      - sdip-internal
      - sdip-data
    restart: unless-stopped

  # ─── DOCUMENT SERVICE ────────────────────────
  document-service:
    build: ./services/document
    environment:
      <<: *common-env
      DB_HOST: postgres
      DB_NAME: sdip_docs
      DB_USER: doc_svc
      MINIO_ENDPOINT: minio:9000
      MINIO_BUCKET: sdip-documents
      AES_KEY_FILE: /run/secrets/aes_encryption_key
      RABBITMQ_URL: amqp://sdip:${RABBITMQ_PASSWORD}@rabbitmq:5672
      MAX_UPLOAD_SIZE: 52428800
    secrets:
      - jwt_public_key
      - db_doc_password
      - aes_encryption_key
    networks:
      - sdip-internal
      - sdip-data
    restart: unless-stopped

  # ─── AI SERVICE ──────────────────────────────
  ai-service:
    build: ./services/ai
    environment:
      <<: *common-env
      QDRANT_HOST: qdrant
      QDRANT_PORT: 6333
      EMBEDDING_MODEL: all-MiniLM-L6-v2
      LLM_PROVIDER: ${LLM_PROVIDER:-ollama}
      OLLAMA_HOST: http://ollama:11434
      RABBITMQ_URL: amqp://sdip:${RABBITMQ_PASSWORD}@rabbitmq:5672
    secrets:
      - jwt_public_key
    networks:
      - sdip-internal
      - sdip-data
    restart: unless-stopped

  # ─── SEARCH SERVICE ─────────────────────────
  search-service:
    build: ./services/search
    environment:
      <<: *common-env
      DB_HOST: postgres
      DB_NAME: sdip_docs
      QDRANT_HOST: qdrant
      QDRANT_PORT: 6333
    secrets:
      - jwt_public_key
      - db_doc_password
    networks:
      - sdip-internal
      - sdip-data
    restart: unless-stopped

  # ─── WORKER SERVICE ─────────────────────────
  worker-service:
    build: ./services/worker
    environment:
      <<: *common-env
      DB_HOST: postgres
      MINIO_ENDPOINT: minio:9000
      QDRANT_HOST: qdrant
      AES_KEY_FILE: /run/secrets/aes_encryption_key
      RABBITMQ_URL: amqp://sdip:${RABBITMQ_PASSWORD}@rabbitmq:5672
    secrets:
      - jwt_public_key
      - db_doc_password
      - aes_encryption_key
    networks:
      - sdip-internal
      - sdip-data
    restart: unless-stopped

  # ─── AUDIT SERVICE ──────────────────────────
  audit-service:
    build: ./services/audit
    environment:
      <<: *common-env
      DB_HOST: postgres
      DB_NAME: sdip_audit
      DB_USER: audit_svc
      RABBITMQ_URL: amqp://sdip:${RABBITMQ_PASSWORD}@rabbitmq:5672
    secrets:
      - jwt_public_key
      - db_audit_password
    networks:
      - sdip-internal
      - sdip-data
    restart: unless-stopped

  # ─── INFRASTRUCTURE ─────────────────────────
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_PASSWORD_FILE: /run/secrets/db_root_password
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d:ro
    secrets:
      - db_root_password
    networks:
      - sdip-data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s
      timeout: 5s
      retries: 5

  rabbitmq:
    image: rabbitmq:3.13-management-alpine
    environment:
      RABBITMQ_DEFAULT_USER: sdip
      RABBITMQ_DEFAULT_PASS: ${RABBITMQ_PASSWORD}
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    networks:
      - sdip-internal
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "check_running"]
      interval: 10s
      timeout: 5s
      retries: 5

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: sdip-admin
      MINIO_ROOT_PASSWORD: ${MINIO_SECRET_KEY}
    volumes:
      - minio_data:/data
    networks:
      - sdip-data
    restart: unless-stopped

  qdrant:
    image: qdrant/qdrant:v1.9.0
    volumes:
      - qdrant_data:/qdrant/storage
    networks:
      - sdip-data
    restart: unless-stopped

volumes:
  pgdata:
  rabbitmq_data:
  minio_data:
  qdrant_data:

networks:
  sdip-external:
    driver: bridge
  sdip-internal:
    driver: bridge
    internal: true    # No external access
  sdip-data:
    driver: bridge
    internal: true    # No external access

secrets:
  jwt_private_key:
    file: ./secrets/jwt_private.pem
  jwt_public_key:
    file: ./secrets/jwt_public.pem
  db_root_password:
    file: ./secrets/db_root_password.txt
  db_auth_password:
    file: ./secrets/db_auth_password.txt
  db_doc_password:
    file: ./secrets/db_doc_password.txt
  db_audit_password:
    file: ./secrets/db_audit_password.txt
  aes_encryption_key:
    file: ./secrets/aes_key.txt
  oauth_google:
    file: ./secrets/oauth_google.txt
  oauth_github:
    file: ./secrets/oauth_github.txt
```

## 8.2 Single-Command Execution

```bash
# Generate secrets (first-time only)
./scripts/generate-secrets.sh

# Launch everything
docker compose up -d --build

# Verify all services are healthy
docker compose ps
docker compose logs -f --tail=50
```

The `generate-secrets.sh` script creates all required secret files with cryptographically random values, generates RSA key pair for JWT signing, and creates self-signed TLS certificates for development.

---

# Section 9: Testing & Security Attack Simulation

## 9.1 Attack Scenarios

### Attack 1: Unauthorized Document Access
```
Scenario: User A tries to access User B's document
Request:  GET /api/documents/{user_b_doc_id}/download
          Authorization: Bearer <user_a_token>

Defense:
  1. Document Service extracts user_id from JWT
  2. Queries document ownership from DB
  3. owner_id ≠ requesting user_id AND role ≠ 'admin'
  4. Returns 403 Forbidden
  5. Publishes security.unauthorized_access audit event

Expected Response: 403 {"error": {"code": "FORBIDDEN", "message": "Access denied"}}
Audit Log: severity=warning, action=security.unauthorized_access
```

### Attack 2: Brute-Force Login
```
Scenario: Attacker sends 100 login attempts with different passwords
Request:  POST /api/auth/login (rapid-fire)

Defense (layered):
  Layer 1 - Nginx rate limit: 5 req/min on /api/auth/ → 429 after burst
  Layer 2 - Account lockout: After 5 failed attempts → account locked 15 min
  Layer 3 - Audit alert: Publishes security.brute_force event

Expected: First 5 attempts → 401; 6th → 423 Locked; Nginx blocks further → 429
```

### Attack 3: Invalid/Expired JWT
```
Scenario: Attacker uses expired or tampered JWT
Request:  GET /api/documents/ with expired token

Defense:
  1. JWT middleware verifies signature (RS256 with public key)
  2. Checks exp claim → token expired
  3. Returns 401 Unauthorized
  4. Publishes security.invalid_token audit event

Tampered token (modified payload):
  1. RS256 signature verification fails
  2. Returns 401 immediately
```

### Attack 4: Malicious File Upload
```
Scenario: Attacker uploads executable disguised as PDF
Request:  POST /api/documents/upload with "report.pdf" (actually .exe)

Defense:
  1. Extension check: .pdf → passes
  2. Magic bytes check: File starts with 4D5A (MZ header) not 25504446 (%PDF)
  3. Mismatch detected → 422 Unprocessable Entity
  4. Publishes security.file_validation_failure event

Scenario 2: Attacker uploads 200MB file
  1. Nginx: client_max_body_size 50m → 413 Payload Too Large (never reaches app)
```

### Attack 5: SQL Injection
```
Scenario: Attacker sends malicious input in search query
Request:  POST /api/search/fulltext
          {"query": "'; DROP TABLE documents; --"}

Defense:
  1. Input validation: Pydantic strips/rejects special characters
  2. SQLAlchemy uses parameterized queries (never string interpolation)
  3. DB user doc_svc has no DROP permission
  4. Three-layer defense prevents any injection

Actual query executed: WHERE to_tsvector('english', title) @@ plainto_tsquery($1)
Parameter: "'; DROP TABLE documents; --" (treated as literal search text)
```

### Attack 6: Rate Limit Abuse (API Scraping)
```
Scenario: Attacker scrapes all endpoints rapidly
Defense:
  1. Nginx global rate: 10 req/s per IP → 429 on excess
  2. Burst allowance: 20 (handles legitimate spikes)
  3. Repeated violations logged as security.rate_limit_exceeded
  4. Optional: fail2ban integration to block IP at firewall level
```

## 9.2 Automated Security Tests

```python
# tests/security/test_auth_attacks.py
import pytest, requests

BASE = "https://localhost/api"

class TestBruteForce:
    def test_account_lockout_after_5_failures(self):
        for i in range(5):
            r = requests.post(f"{BASE}/auth/login",
                json={"email": "victim@test.com", "password": f"wrong{i}"}, verify=False)
            assert r.status_code == 401
        # 6th attempt → locked
        r = requests.post(f"{BASE}/auth/login",
            json={"email": "victim@test.com", "password": "wrong5"}, verify=False)
        assert r.status_code == 423

class TestUnauthorizedAccess:
    def test_cannot_access_other_users_document(self, user_a_token, user_b_doc_id):
        r = requests.get(f"{BASE}/documents/{user_b_doc_id}",
            headers={"Authorization": f"Bearer {user_a_token}"}, verify=False)
        assert r.status_code == 403

class TestFileUpload:
    def test_rejects_executable_disguised_as_pdf(self, auth_token):
        exe_content = b'\x4d\x5a' + b'\x00' * 100  # MZ header
        r = requests.post(f"{BASE}/documents/upload",
            headers={"Authorization": f"Bearer {auth_token}"},
            files={"file": ("report.pdf", exe_content, "application/pdf")}, verify=False)
        assert r.status_code == 422

class TestTokenSecurity:
    def test_expired_token_rejected(self, expired_token):
        r = requests.get(f"{BASE}/documents/",
            headers={"Authorization": f"Bearer {expired_token}"}, verify=False)
        assert r.status_code == 401

    def test_tampered_token_rejected(self, valid_token):
        tampered = valid_token[:-5] + "XXXXX"
        r = requests.get(f"{BASE}/documents/",
            headers={"Authorization": f"Bearer {tampered}"}, verify=False)
        assert r.status_code == 401
```

---

# Section 10: Deployment Notes, Conclusion & Team Roles

## 10.1 Deployment Checklist

| Step | Command / Action |
|---|---|
| 1. Clone repo | `git clone <repo-url> && cd sdip` |
| 2. Generate secrets | `bash scripts/generate-secrets.sh` |
| 3. Configure OAuth | Fill in OAuth client IDs/secrets in `.env` |
| 4. Generate TLS cert | `bash scripts/generate-tls-cert.sh` (self-signed for dev) |
| 5. Launch | `docker compose up -d --build` |
| 6. Init database | Runs automatically via `docker-entrypoint-initdb.d` scripts |
| 7. Create admin user | `docker compose exec auth-service node scripts/create-admin.js` |
| 8. Verify | `docker compose ps` — all services "healthy" |
| 9. Access | `https://localhost` |

## 10.2 Production Hardening Notes

- Replace self-signed certs with Let's Encrypt (certbot sidecar)
- Enable PostgreSQL `ssl = on` with server certificates
- Set up log aggregation (ELK Stack or Loki + Grafana)
- Add Prometheus metrics endpoint to each service
- Configure RabbitMQ clustering for HA
- Use Docker Swarm or Kubernetes for multi-node deployment
- Enable MinIO erasure coding for data durability
- Set up automated database backups with `pg_dump` + GPG encryption

## 10.3 Conclusion

The **Secure Document Intelligence Platform** demonstrates a production-grade distributed system with:

- **7 microservices** with clear bounded contexts
- **Defense-in-depth security** across all layers (network, transport, application, data)
- **AI capabilities** via RAG pipeline with vector search
- **Async processing** with RabbitMQ for scalability
- **Complete audit trail** for regulatory compliance
- **Single-command deployment** via Docker Compose

Every security requirement is addressed with concrete implementations, not abstract descriptions. The system is designed to be both academically rigorous and practically deployable.

## 10.4 Team Role Distribution (5-Member Team)

| Member | Role | Responsibilities |
|---|---|---|
| **Member 1** | System Architect & Auth Lead | Architecture design, Auth Service, JWT/OAuth implementation, RBAC middleware, secrets management |
| **Member 2** | Document & Storage Engineer | Document Service, MinIO integration, file encryption (AES-256-GCM), integrity verification (SHA-256), upload validation |
| **Member 3** | AI & Search Engineer | AI Service, embedding pipeline, Qdrant integration, RAG implementation, Search Service (hybrid search) |
| **Member 4** | DevOps & Infrastructure Lead | Docker Compose, Nginx configuration, TLS/HTTPS setup, RabbitMQ topology, Worker Service, CI/CD pipeline |
| **Member 5** | Security & Audit Engineer | Audit/Logging Service, security attack simulations, automated security tests, database security, penetration testing |

### Shared Responsibilities
- All members: Code review, documentation, integration testing
- Members 1 & 5: Security architecture review
- Members 2 & 3: Data pipeline integration (document → chunks → vectors)
- Members 3 & 4: Worker service integration (queue → AI processing)

## 10.5 Logging & Audit Strategy Summary

| Event Category | Examples | Severity | Retention |
|---|---|---|---|
| Authentication | Login, logout, failed login, OAuth, token refresh | info/warning | 1 year |
| Document Actions | Upload, download, delete, share | info | 1 year |
| Admin Actions | Role change, user deactivation, config updates | warning | 2 years |
| Security Violations | Rate limit hit, invalid token, unauthorized access, malicious upload | critical | 3 years |

**Tamper Detection:** Each audit log entry includes a SHA-256 checksum computed over `(timestamp + user_id + action + details + previous_checksum)`, creating a hash chain that makes retrospective tampering detectable.
