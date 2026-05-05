# 🔐 Secure Document Intelligence Platform (SDIP)

A production-grade secure distributed system featuring AI-powered document analysis, encrypted storage, and comprehensive audit logging.

## Architecture Overview

```
Client → Nginx (HTTPS/TLS 1.3) → Microservices → RabbitMQ → Workers
                                       ↓
                              PostgreSQL · MinIO · Qdrant
```

| Service | Technology | Port | Purpose |
|---------|-----------|------|---------|
| **API Gateway** | Nginx | 443 | SSL termination, rate limiting, routing |
| **Auth Service** | Node.js/Express | 3001 | JWT auth, OAuth, RBAC |
| **Document Service** | Python/FastAPI | 3002 | File upload, AES-256 encryption, integrity |
| **AI Service** | Python/FastAPI | 3003 | RAG pipeline, embeddings, LLM inference |
| **Search Service** | Python/FastAPI | 3004 | Full-text + semantic + hybrid search |
| **Worker Service** | Python | 3005 | Async document processing & embedding |
| **Audit Service** | Python/FastAPI | 3006 | Centralized audit logging |
| **PostgreSQL** | PostgreSQL 16 | 5432 | Primary database |
| **RabbitMQ** | RabbitMQ 3.13 | 5672 | Message queue |
| **MinIO** | MinIO | 9000 | S3-compatible object storage |
| **Qdrant** | Qdrant | 6333 | Vector database |

## Quick Start

### Prerequisites
- Docker & Docker Compose v2
- OpenSSL (for secret generation)
- Bash shell (Git Bash on Windows)

### 1. Generate Secrets & TLS Certificates
```bash
bash scripts/generate-secrets.sh
bash scripts/generate-tls-cert.sh
```

### 2. Launch All Services
```bash
docker compose up -d --build
```

### 3. Create Admin User
```bash
docker compose exec auth-service node scripts/create-admin.js
```

### 4. Verify
```bash
docker compose ps                          # All services healthy
curl -k https://localhost/health           # Nginx responding
curl -k https://localhost/api/auth/me      # Auth check (will return 401)
```

## Security Features

| Feature | Implementation |
|---------|---------------|
| Authentication | JWT (RS256, 15-min access tokens) |
| Password Hashing | bcrypt (cost factor 12) |
| OAuth SSO | Google, GitHub via Passport.js |
| RBAC | Admin/User roles enforced on every route |
| HTTPS | TLS 1.2/1.3, HSTS, security headers |
| Rate Limiting | Nginx zones (10r/s global, 5r/m auth) |
| File Encryption | AES-256-GCM at rest |
| Integrity | SHA-256 hash verification on download |
| Input Validation | Pydantic models + express-validator |
| File Validation | Extension allowlist + magic byte verification |
| Secrets Management | Docker secrets (file-based) |
| Network Isolation | 3 Docker networks (external/internal/data) |
| DB Security | Least-privilege roles per service |
| Audit Trail | Tamper-evident hash chain on all events |
| Brute-Force Protection | Account lockout after 5 failed attempts |

## Running Security Tests

```bash
pip install pytest requests
pytest tests/security/ -v
```

## Project Structure

```
Project/
├── docker-compose.yml          # Complete orchestration
├── .env.example                # Environment template
├── nginx/
│   ├── nginx.conf              # Reverse proxy + rate limiting + TLS
│   └── ssl/                    # TLS certificates (generated)
├── db/
│   └── init/01-init.sql        # Database schemas + roles
├── scripts/
│   ├── generate-secrets.sh     # Secret key generator
│   └── generate-tls-cert.sh    # Self-signed cert generator
├── services/
│   ├── auth/                   # Node.js Auth Service
│   │   ├── Dockerfile
│   │   ├── package.json
│   │   ├── scripts/create-admin.js
│   │   └── src/
│   │       ├── index.js
│   │       ├── routes/auth.js
│   │       ├── routes/users.js
│   │       └── middleware/
│   │           ├── auth.js
│   │           └── passport.js
│   ├── document/               # Python Document Service
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/main.py
│   ├── ai/                     # Python AI/RAG Service
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/main.py
│   ├── search/                 # Python Search Service
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/main.py
│   ├── worker/                 # Python Worker Service
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   └── app/worker.py
│   └── audit/                  # Python Audit Service
│       ├── Dockerfile
│       ├── requirements.txt
│       └── app/main.py
├── tests/
│   └── security/test_attacks.py  # Attack simulation tests
├── secrets/                    # Generated secrets (gitignored)
└── SDIP_Design_Sections_*.md   # Full design documentation
```

## Design Documentation

- **Sections 1–5**: System Overview, Architecture, Microservices, Security, Database
- **Sections 6–10**: Message Queues, API Spec, Docker Compose, Attack Simulations, Deployment

## License

University Final Project — For educational purposes.
