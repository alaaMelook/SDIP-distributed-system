# 🔐 Secure Document Intelligence Platform (SDIP)

A production-grade **secure distributed system** featuring AI-powered document analysis (RAG), AES-256 encryption at rest, comprehensive audit logging, and role-based access control — built with 11 microservices orchestrated via Docker Compose.

---

## 📐 Architecture Overview

```
                          ┌────────────────┐
                          │   Web Client   │
                          └───────┬────────┘
                                  │ HTTPS (TLS 1.3)
                          ┌───────▼────────┐
                          │  Nginx Gateway │  ← Rate Limiting · Security Headers · HSTS
                          └──┬──┬──┬──┬──┬─┘
            ┌────────────────┘  │  │  │  └────────────────┐
            ▼                   ▼  │  ▼                   ▼
     ┌──────────┐    ┌──────────┐  │ ┌──────────┐  ┌──────────┐
     │   Auth   │    │ Document │  │ │  Search  │  │  Audit   │
     │ Service  │    │ Service  │  │ │ Service  │  │ Service  │
     └────┬─────┘    └────┬─────┘  │ └────┬─────┘  └────┬─────┘
          │               │        │      │              │
          │          ┌────▼────┐   │ ┌────▼────┐         │
          │          │  MinIO  │   │ │ Qdrant  │         │
          │          │ Storage │   │ │ VectorDB│         │
          │          └─────────┘   │ └─────────┘         │
          │                   ┌────▼─────┐               │
          │                   │    AI    │               │
          │                   │ Service  │               │
          │                   └──────────┘               │
          │          ┌─────────────────┐                 │
          └──────────►    RabbitMQ     ◄─────────────────┘
                     └───────┬─────────┘
                             │
                      ┌──────▼───────┐
                      │   Worker     │  ← Async Processing · Embedding Generation
                      │   Service    │
                      └──────────────┘
                             │
                      ┌──────▼───────┐
                      │  PostgreSQL  │
                      │     16       │
                      └──────────────┘
```

### Services

| # | Service | Technology | Port | Purpose |
|---|---------|-----------|------|---------|
| 1 | **API Gateway** | Nginx 1.27 | 443/80 | HTTPS termination, rate limiting, routing, security headers |
| 2 | **Auth Service** | Node.js / Express | 3001 | JWT (RS256), OAuth (Google/GitHub), RBAC, brute-force protection |
| 3 | **Document Service** | Python / FastAPI | 3002 | Secure file upload, AES-256-GCM encryption, integrity verification |
| 4 | **AI Service** | Python / FastAPI | 3003 | RAG pipeline, embeddings (MiniLM), LLM inference (Ollama/OpenAI) |
| 5 | **Search Service** | Python / FastAPI | 3004 | Full-text + semantic + hybrid search (RRF fusion) |
| 6 | **Worker Service** | Python | — | Async document processing, text extraction, embedding generation |
| 7 | **Audit Service** | Python / FastAPI | 3006 | Centralized audit logging, security event monitoring, admin dashboard |
| 8 | **PostgreSQL** | PostgreSQL 16 | 5432 | Primary database (3 isolated databases: auth, docs, audit) |
| 9 | **RabbitMQ** | RabbitMQ 3.13 | 5672 | Message queue with dead-letter exchange (DLX) |
| 10 | **MinIO** | MinIO | 9000 | S3-compatible encrypted object storage |
| 11 | **Qdrant** | Qdrant 1.9 | 6333 | Vector database for semantic search |

---

## 🚀 How to Run (Step by Step)

### Prerequisites
- **Docker Desktop** installed and running
- **Git Bash** (comes with Git for Windows) or any Bash shell
- **Node.js** (for the frontend dev server, optional)

---

### Step 1 — Clone the Repository
```bash
git clone <repository-url>
cd Project
```

### Step 2 — Generate Secrets & TLS Certificates

Open **Git Bash** (not PowerShell) and run:
```bash
# Generate JWT keys, database passwords, AES encryption key
bash scripts/generate-secrets.sh

# Generate self-signed TLS certificate for HTTPS
bash scripts/generate-tls-cert.sh
```

> **What this creates:**
> - `secrets/jwt_private.pem` / `jwt_public.pem` — RSA key pair for JWT signing
> - `secrets/db_*_password.txt` — Unique passwords for each database service role
> - `secrets/aes_key.txt` — 256-bit AES encryption key for file encryption
> - `nginx/ssl/sdip.crt` / `sdip.key` — Self-signed TLS certificate

### Step 3 — Configure Environment
```bash
cp .env.example .env
```
Edit `.env` if you want to add **OAuth credentials** (Google/GitHub). This is optional.

### Step 4 — Launch All Services with Docker Compose
```bash
docker compose up -d --build
```

> This starts **11 containers**: Nginx, Auth, Document, AI, Search, Worker, Audit, PostgreSQL, RabbitMQ, MinIO, Qdrant.
> First run takes a few minutes to build images and download dependencies.

### Step 5 — Wait for Services to be Healthy

Check that all services are running:
```powershell
# PowerShell
docker compose ps
```
Wait until the **STATUS** column shows `(healthy)` for auth, document, and audit services.

### Step 6 — Create an Admin User
```bash
docker compose exec auth-service node scripts/create-admin.js
```
You will be asked for:
- **Email**: e.g. `admin@sdip.local`
- **Password**: e.g. `Admin@SDIP2024!` (must have 8+ chars, uppercase, lowercase, number, symbol)
- **Display Name**: e.g. `System Admin`

### Step 7 — Start the Frontend Dashboard
Open a **new terminal** (PowerShell):
```powershell
npx -y http-server ./frontend -p 8080 -c-1 --cors
```

### Step 8 — Open in Browser
| Interface | URL | Description |
|-----------|-----|-------------|
| 🖥️ **Frontend Dashboard** | http://localhost:8080 | Login, upload docs, view audit logs |
| 🔐 **API Gateway (HTTPS)** | https://localhost | Nginx gateway (accept self-signed cert warning) |
| 🐰 **RabbitMQ Management** | http://localhost:15672 | Message queue dashboard |

### Step 9 — Login
Open http://localhost:8080 in your browser:
1. Enter the **email** and **password** you created in Step 6
2. Click **Sign In**
3. You'll see the Dashboard with system stats, service health, and recent activity

---

### ⚡ Quick Command Summary (Copy & Paste)
```bash
# 1. Generate secrets (run in Git Bash)
bash scripts/generate-secrets.sh
bash scripts/generate-tls-cert.sh

# 2. Start everything
docker compose up -d --build

# 3. Create admin user
docker compose exec auth-service node scripts/create-admin.js

# 4. Start frontend (new terminal)
npx -y http-server ./frontend -p 8080 -c-1 --cors

# 5. Open browser → http://localhost:8080
```

---

### 🔍 Verify Services are Working

```powershell
# Check service health (PowerShell)
Invoke-RestMethod -Uri "http://localhost:3001/health"   # Auth
Invoke-RestMethod -Uri "http://localhost:3002/health"   # Document
Invoke-RestMethod -Uri "http://localhost:3006/health"   # Audit
```

### 📋 Example API Usage (PowerShell)

```powershell
# Login and get JWT token
$response = Invoke-RestMethod -Uri "http://localhost:3001/auth/login" `
  -Method POST -ContentType "application/json" `
  -Body '{"email":"admin@sdip.local","password":"Admin@SDIP2024!"}'
$token = $response.access_token

# View your profile
Invoke-RestMethod -Uri "http://localhost:3001/auth/me" `
  -Headers @{Authorization="Bearer $token"}

# List your documents
Invoke-RestMethod -Uri "http://localhost:3002/documents/" `
  -Headers @{Authorization="Bearer $token"}

# View audit logs (admin only)
Invoke-RestMethod -Uri "http://localhost:3006/audit/logs" `
  -Headers @{Authorization="Bearer $token"}
```

### 🛑 Stop All Services
```bash
docker compose down        # Stop containers
docker compose down -v     # Stop and remove data volumes (full reset)
```

---

## 🔒 Security Features — 20/20 Requirements

### Authentication & Authorization

| # | Requirement | Implementation |
|---|-------------|---------------|
| 1 | **Authentication** | JWT with RS256 algorithm, 15-min access tokens, 7-day refresh tokens |
| 2 | **Password Hashing** | bcrypt with cost factor 12, never logged in plaintext |
| 3 | **RBAC** | Admin/User roles enforced via middleware on every route |
| 4 | **OAuth SSO** | Google & GitHub via Passport.js, profile linking, JWT issued after OAuth |

### Network & API Security

| # | Requirement | Implementation |
|---|-------------|---------------|
| 5 | **API Gateway** | Nginx routing to all services, internal services not publicly exposed |
| 6 | **HTTPS** | TLS 1.2/1.3, HSTS, HTTP→HTTPS redirect, self-signed certs |
| 7 | **Rate Limiting** | Nginx zones: 10r/s global, 5r/min auth, 2r/s upload |
| 8 | **Input Validation** | express-validator (Node.js), Pydantic models (Python) |

### Data Security

| # | Requirement | Implementation |
|---|-------------|---------------|
| 9 | **Secure File Upload** | Extension whitelist (.pdf/.docx/.txt/.md), magic byte verification, blocked executables (.exe/.sh/.bat), 50MB size limit |
| 10 | **File Encryption** | AES-256-GCM encryption at rest in MinIO, per-file IV (nonce) |
| 11 | **Integrity Verification** | SHA-256 hash computed on upload, verified on every download |
| 12 | **Service-to-Service Auth** | All services verify JWT public key, no blind trust between services |
| 13 | **Secrets Management** | Docker secrets (file-based), .env for non-sensitive config, `.gitignore` configured |
| 14 | **Database Security** | 3 isolated databases, least-privilege roles per service, hashed passwords, audit tables |

### Infrastructure & Observability

| # | Requirement | Implementation |
|---|-------------|---------------|
| 15 | **Message Queue** | RabbitMQ: document events → async processing + embedding generation |
| 16 | **Queue Security** | Custom user `sdip` with strong password, dead-letter exchange for failures |
| 17 | **Audit Trail** | Tamper-evident hash chain, logs: login/logout/upload/download/unauthorized access/admin actions |
| 18 | **Monitoring Dashboard** | Frontend: system stats, service health, recent activity, audit viewer |
| 19 | **Error Handling** | Safe error messages, no stack traces exposed, internal-only logging |
| 20 | **Docker Compose** | Full orchestration with 11 containers, 3 isolated networks, health checks |

---

## 🛡️ Attack Simulation Tests

Run the automated security test suite:
```bash
pip install pytest requests
python -m pytest tests/security/ -v --disable-warnings
```

### Test Coverage

| Attack | Test | Expected Result |
|--------|------|-----------------|
| **Brute Force** | 5+ failed logins | Account locked for 15 minutes (HTTP 423) |
| **Token Tampering** | Modified JWT signature | Rejected with HTTP 401 |
| **Missing Token** | No Authorization header | Rejected with HTTP 401 |
| **Cross-User Access** | User B accesses User A's document | Rejected with HTTP 403 |
| **Malicious Upload** | .exe disguised as .pdf | Rejected with HTTP 422 |
| **Forbidden Extension** | .exe, .bat, .sh files | Rejected with HTTP 422 |
| **Oversized File** | >50MB upload | Rejected with HTTP 413 |
| **SQL Injection** | `'; DROP TABLE users; --` | Treated as literal string, no SQL error |
| **RBAC Bypass** | User tries admin endpoints | Rejected with HTTP 403 |
| **Rate Limiting** | Rapid auth requests via Nginx | Blocked with HTTP 429 |

---

## 📁 Project Structure

```
Project/
├── docker-compose.yml              # Complete orchestration (11 services)
├── .env.example                    # Environment template
├── .gitignore                      # Secrets/certs excluded
│
├── nginx/
│   ├── nginx.conf                  # API Gateway: routing + TLS + rate limiting + headers
│   └── ssl/                        # TLS certificates (generated)
│
├── db/
│   └── init/01-init.sh             # Database schemas + least-privilege roles
│
├── scripts/
│   ├── generate-secrets.sh         # Generates JWT keys, DB passwords, AES key, OAuth placeholders
│   └── generate-tls-cert.sh        # Self-signed TLS certificate generator
│
├── services/
│   ├── auth/                       # Node.js Auth Service
│   │   ├── Dockerfile
│   │   ├── package.json
│   │   ├── scripts/create-admin.js # Admin user setup script
│   │   └── src/
│   │       ├── index.js            # Express app, JWT keys, RabbitMQ
│   │       ├── routes/auth.js      # Register, login, refresh, logout, me
│   │       ├── routes/users.js     # Admin: list users, change roles
│   │       └── middleware/
│   │           ├── auth.js         # JWT verification + RBAC
│   │           └── passport.js     # Google & GitHub OAuth strategies
│   │
│   ├── document/                   # Python Document Service
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/main.py            # Upload, download, delete, AES-256, SHA-256
│   │
│   ├── ai/                         # Python AI/RAG Service
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/main.py            # RAG query, embedding, LLM inference
│   │
│   ├── search/                     # Python Search Service
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/main.py            # Full-text + semantic + hybrid (RRF) search
│   │
│   ├── worker/                     # Python Worker Service
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/worker.py          # RabbitMQ consumer: text extraction + embedding
│   │
│   └── audit/                      # Python Audit Service
│       ├── Dockerfile
│       ├── requirements.txt
│       └── app/main.py            # Event consumer, query APIs, admin stats
│
├── frontend/                       # Web Dashboard
│   ├── index.html                  # SPA: login, dashboard, documents, audit, users
│   ├── styles.css                  # Dark theme design system
│   └── app.js                     # API integration, auth, navigation
│
├── tests/
│   └── security/test_attacks.py    # 10 attack simulation tests
│
├── secrets/                        # Generated secrets (gitignored)
├── PROJECT_DOCUMENTATION.md        # Full technical documentation
├── SDIP_Design_Sections_1_to_5.md  # Architecture & security design
└── SDIP_Design_Sections_6_to_10.md # API specs, deployment, attack simulations
```

---

## 🌐 API Quick Reference

### Auth Service (`/auth`)
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/auth/register` | — | Create new account |
| POST | `/auth/login` | — | Login, get JWT tokens |
| POST | `/auth/refresh` | — | Refresh access token |
| POST | `/auth/logout` | Bearer | Revoke refresh token |
| GET | `/auth/me` | Bearer | Get current user profile |
| GET | `/auth/users` | Admin | List all users |
| PUT | `/auth/users/:id/role` | Admin | Change user role |
| GET | `/auth/oauth/google` | — | Initiate Google OAuth |
| GET | `/auth/oauth/github` | — | Initiate GitHub OAuth |

### Document Service (`/documents`)
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/documents/upload` | Bearer | Upload, validate, encrypt, store |
| GET | `/documents/` | Bearer | List user's documents |
| GET | `/documents/:id` | Bearer | Get document metadata |
| GET | `/documents/:id/download` | Bearer | Decrypt & download (integrity verified) |
| DELETE | `/documents/:id` | Bearer | Soft delete document |
| GET | `/documents/admin/all` | Admin | List all documents |

### AI Service (`/ai`)
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/ai/query` | Bearer | RAG: question → context retrieval → LLM answer |
| POST | `/ai/embed/:id` | Bearer | Generate embeddings for a document |
| DELETE | `/ai/vectors/:id` | Bearer | Remove document vectors |

### Search Service (`/search`)
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| POST | `/search/fulltext` | Bearer | PostgreSQL full-text search |
| POST | `/search/semantic` | Bearer | Qdrant vector similarity search |
| POST | `/search/hybrid` | Bearer | Reciprocal Rank Fusion (RRF) |

### Audit Service (`/audit`)
| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| GET | `/audit/logs` | Admin | Query audit logs with filters |
| GET | `/audit/logs/user/:id` | Admin | Logs for specific user |
| GET | `/audit/security-events` | Admin | Security-only events |
| GET | `/audit/stats` | Admin | Event statistics by period |

---

## 🔧 Network Isolation

```
┌─────────────────────────────────────────────────────┐
│  sdip-external (bridge)                             │
│  Nginx ↔ Auth ↔ Document ↔ Audit                   │
├─────────────────────────────────────────────────────┤
│  sdip-internal (internal)                           │
│  All services ↔ RabbitMQ (no external access)       │
├─────────────────────────────────────────────────────┤
│  sdip-data (internal)                               │
│  Services ↔ PostgreSQL ↔ MinIO ↔ Qdrant             │
│  (no external access)                               │
└─────────────────────────────────────────────────────┘
```

---

## 📖 Design Documentation

| Document | Contents |
|----------|----------|
| `SDIP_Design_Sections_1_to_5.md` | System overview, architecture, microservices, security, database design |
| `SDIP_Design_Sections_6_to_10.md` | Message queues, API spec, Docker Compose, attack simulations, deployment |
| `PROJECT_DOCUMENTATION.md` | Full technical project documentation |

---

## 👥 Team

University Final Project — Secure Distributed System Design & Implementation

---

## License

For educational purposes only.
