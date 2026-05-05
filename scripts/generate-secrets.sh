#!/usr/bin/env bash
# ─── SDIP Secrets Generator ─────────────────────
# Generates all required secret files for the platform
set -euo pipefail

SECRETS_DIR="./secrets"
mkdir -p "$SECRETS_DIR"

echo "🔐 Generating SDIP secrets..."

# ─── Random password generator ───────────────────
gen_password() {
    openssl rand -base64 48 | tr -d '/+=' | head -c 64
}

# ─── JWT RSA Key Pair ────────────────────────────
if [ ! -f "$SECRETS_DIR/jwt_private.pem" ]; then
    echo "  → Generating RSA 2048-bit key pair for JWT..."
    openssl genrsa -out "$SECRETS_DIR/jwt_private.pem" 2048 2>/dev/null
    openssl rsa -in "$SECRETS_DIR/jwt_private.pem" -pubout -out "$SECRETS_DIR/jwt_public.pem" 2>/dev/null
else
    echo "  ✓ JWT keys already exist, skipping."
fi

# ─── Database Passwords ─────────────────────────
for name in db_root_password db_auth_password db_doc_password db_audit_password; do
    file="$SECRETS_DIR/${name}.txt"
    if [ ! -f "$file" ]; then
        gen_password > "$file"
        echo "  → Generated $name"
    else
        echo "  ✓ $name already exists, skipping."
    fi
done

# ─── AES Encryption Key (32 bytes hex) ──────────
if [ ! -f "$SECRETS_DIR/aes_key.txt" ]; then
    openssl rand -hex 32 > "$SECRETS_DIR/aes_key.txt"
    echo "  → Generated AES-256 encryption key"
else
    echo "  ✓ AES key already exists, skipping."
fi

# ─── OAuth Placeholder Secrets ───────────────────
for name in oauth_google oauth_github; do
    file="$SECRETS_DIR/${name}.txt"
    if [ ! -f "$file" ]; then
        echo "REPLACE_WITH_REAL_SECRET" > "$file"
        echo "  → Created placeholder for $name (update before production!)"
    else
        echo "  ✓ $name already exists, skipping."
    fi
done

# ─── Generate .env from template ────────────────
if [ ! -f ".env" ]; then
    RABBITMQ_PW=$(gen_password)
    MINIO_PW=$(gen_password)
    cat > .env <<EOF
RABBITMQ_PASSWORD=${RABBITMQ_PW}
MINIO_SECRET_KEY=${MINIO_PW}
OAUTH_GOOGLE_CLIENT_ID=your-google-client-id
OAUTH_GITHUB_CLIENT_ID=your-github-client-id
LLM_PROVIDER=ollama
OPENAI_API_KEY=
EOF
    echo "  → Generated .env file"
else
    echo "  ✓ .env already exists, skipping."
fi

echo ""
echo "✅ All secrets generated in $SECRETS_DIR/"
echo "⚠️  Remember to update OAuth secrets before deployment!"
