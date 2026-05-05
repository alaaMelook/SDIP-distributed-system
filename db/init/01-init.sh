#!/bin/bash
# =============================================================
# SDIP Database Initialization Script
# Runs automatically on first PostgreSQL container start
# =============================================================

set -e

# Read passwords from Docker secrets
AUTH_PW=$(cat /run/secrets/db_auth_password 2>/dev/null || echo 'auth_secret')
DOC_PW=$(cat /run/secrets/db_doc_password 2>/dev/null || echo 'doc_secret')
AUDIT_PW=$(cat /run/secrets/db_audit_password 2>/dev/null || echo 'audit_secret')

echo "🔧 Creating databases and roles..."

# ─── Create Databases and Roles ─────────────────
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE sdip_auth;
    CREATE DATABASE sdip_docs;
    CREATE DATABASE sdip_audit;

    DO \$\$
    BEGIN
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'auth_svc') THEN
        CREATE ROLE auth_svc LOGIN PASSWORD '$AUTH_PW';
      END IF;
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'doc_svc') THEN
        CREATE ROLE doc_svc LOGIN PASSWORD '$DOC_PW';
      END IF;
      IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'audit_svc') THEN
        CREATE ROLE audit_svc LOGIN PASSWORD '$AUDIT_PW';
      END IF;
    END
    \$\$;
EOSQL

echo "✅ Databases and roles created"

# =============================================================
# AUTH DATABASE SCHEMA
# =============================================================
echo "📋 Initializing sdip_auth schema..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "sdip_auth" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";

    CREATE TABLE IF NOT EXISTS users (
        id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        email                 VARCHAR(255) UNIQUE NOT NULL,
        password_hash         VARCHAR(255),
        role                  VARCHAR(20) NOT NULL DEFAULT 'user'
                              CHECK (role IN ('user', 'admin')),
        display_name          VARCHAR(255) NOT NULL,
        avatar_url            VARCHAR(500),
        oauth_provider        VARCHAR(50),
        oauth_id              VARCHAR(255),
        is_active             BOOLEAN NOT NULL DEFAULT true,
        failed_login_attempts INT NOT NULL DEFAULT 0,
        locked_until          TIMESTAMP WITH TIME ZONE,
        created_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        UNIQUE(oauth_provider, oauth_id)
    );

    CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
    CREATE INDEX IF NOT EXISTS idx_users_oauth ON users(oauth_provider, oauth_id);

    CREATE TABLE IF NOT EXISTS refresh_tokens (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash  VARCHAR(255) NOT NULL,
        device_info VARCHAR(500),
        expires_at  TIMESTAMP WITH TIME ZONE NOT NULL,
        created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        revoked_at  TIMESTAMP WITH TIME ZONE
    );

    CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);
    CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens(token_hash);

    -- Grant permissions to auth_svc
    GRANT CONNECT ON DATABASE sdip_auth TO auth_svc;
    GRANT USAGE ON SCHEMA public TO auth_svc;
    GRANT SELECT, INSERT, UPDATE, DELETE ON users, refresh_tokens TO auth_svc;
EOSQL

# =============================================================
# DOCUMENTS DATABASE SCHEMA
# =============================================================
echo "📋 Initializing sdip_docs schema..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "sdip_docs" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";

    CREATE TABLE IF NOT EXISTS documents (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        owner_id        UUID NOT NULL,
        title           VARCHAR(255) NOT NULL,
        description     TEXT DEFAULT '',
        file_name       VARCHAR(255) NOT NULL,
        file_size       BIGINT NOT NULL,
        mime_type       VARCHAR(100) NOT NULL,
        storage_key     VARCHAR(500) NOT NULL,
        encryption_iv   BYTEA NOT NULL,
        sha256_hash     VARCHAR(64) NOT NULL,
        is_embedded     BOOLEAN NOT NULL DEFAULT false,
        is_deleted      BOOLEAN NOT NULL DEFAULT false,
        deleted_at      TIMESTAMP WITH TIME ZONE,
        created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_id);
    CREATE INDEX IF NOT EXISTS idx_documents_deleted ON documents(is_deleted);
    CREATE INDEX IF NOT EXISTS idx_documents_fulltext
        ON documents USING GIN(to_tsvector('english', title || ' ' || description));

    CREATE TABLE IF NOT EXISTS document_chunks (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        document_id     UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        chunk_index     INT NOT NULL,
        content         TEXT NOT NULL,
        token_count     INT NOT NULL,
        qdrant_point_id UUID NOT NULL,
        created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id);

    CREATE TABLE IF NOT EXISTS tags (
        id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name VARCHAR(50) UNIQUE NOT NULL
    );

    CREATE TABLE IF NOT EXISTS document_tags (
        document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
        tag_id      UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (document_id, tag_id)
    );

    -- Grant permissions to doc_svc
    GRANT CONNECT ON DATABASE sdip_docs TO doc_svc;
    GRANT USAGE ON SCHEMA public TO doc_svc;
    GRANT SELECT, INSERT, UPDATE, DELETE ON documents, document_chunks, tags, document_tags TO doc_svc;
EOSQL

# =============================================================
# AUDIT DATABASE SCHEMA
# =============================================================
echo "📋 Initializing sdip_audit schema..."
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "sdip_audit" <<-EOSQL
    CREATE EXTENSION IF NOT EXISTS "pgcrypto";

    CREATE TABLE IF NOT EXISTS audit_logs (
        id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        timestamp     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        user_id       UUID,
        action        VARCHAR(100) NOT NULL,
        resource_type VARCHAR(50),
        resource_id   UUID,
        ip_address    INET,
        user_agent    VARCHAR(500),
        details       JSONB DEFAULT '{}',
        severity      VARCHAR(20) NOT NULL DEFAULT 'info'
                      CHECK (severity IN ('info', 'warning', 'error', 'critical')),
        checksum      VARCHAR(64) NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_logs(user_id);
    CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
    CREATE INDEX IF NOT EXISTS idx_audit_severity ON audit_logs(severity);

    -- Grant permissions to audit_svc
    GRANT CONNECT ON DATABASE sdip_audit TO audit_svc;
    GRANT USAGE ON SCHEMA public TO audit_svc;
    GRANT SELECT, INSERT ON audit_logs TO audit_svc;
EOSQL

echo "✅ All databases initialized successfully!"
