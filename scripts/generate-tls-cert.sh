#!/usr/bin/env bash
# ─── SDIP TLS Certificate Generator ─────────────
# Generates self-signed certificates for development
set -euo pipefail

SSL_DIR="./nginx/ssl"
mkdir -p "$SSL_DIR"

if [ -f "$SSL_DIR/sdip.crt" ]; then
    echo "✓ TLS certificate already exists, skipping."
    exit 0
fi

echo "🔒 Generating self-signed TLS certificate..."

openssl req -x509 -nodes -days 365 \
    -newkey rsa:2048 \
    -keyout "$SSL_DIR/sdip.key" \
    -out "$SSL_DIR/sdip.crt" \
    -subj "/C=US/ST=State/L=City/O=SDIP/OU=Dev/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:sdip.local,IP:127.0.0.1" \
    2>/dev/null

echo "✅ TLS certificate generated at $SSL_DIR/"
echo "   Certificate: $SSL_DIR/sdip.crt"
echo "   Private Key: $SSL_DIR/sdip.key"
