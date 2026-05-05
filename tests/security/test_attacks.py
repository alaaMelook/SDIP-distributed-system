"""
SDIP Security Attack Simulation Tests
Run: python -m pytest tests/security/ -v --disable-warnings
Requires the system to be running via docker compose up
"""
import pytest
import requests
import time

# ─── Service URLs (direct, bypassing Nginx) ──────
AUTH_URL = "http://localhost:3001/auth"
DOC_URL = "http://localhost:3002/documents"
SEARCH_URL = "http://localhost:3004/search"
AUDIT_URL = "http://localhost:3006/audit"

# Nginx gateway URL (used only for rate-limit & TLS tests)
NGINX_URL = "https://localhost/api"
VERIFY_SSL = False  # Self-signed cert in dev


# ─── Fixtures ────────────────────────────────────
@pytest.fixture(scope="module")
def admin_token():
    """Register and login an admin user for testing."""
    r = requests.post(f"{AUTH_URL}/register", json={
        "email": "admin_test@sdip.local",
        "password": "Admin@12345!",
        "display_name": "Test Admin"
    })
    if r.status_code in (201, 409):  # Created or already exists
        r = requests.post(f"{AUTH_URL}/login", json={
            "email": "admin_test@sdip.local",
            "password": "Admin@12345!"
        })
        return r.json().get("access_token")
    pytest.skip("Could not create admin user")


@pytest.fixture(scope="module")
def user_a():
    """Register User A and return tokens."""
    requests.post(f"{AUTH_URL}/register", json={
        "email": "user_a@sdip.local", "password": "UserA@12345!", "display_name": "User A"
    })
    r = requests.post(f"{AUTH_URL}/login", json={
        "email": "user_a@sdip.local", "password": "UserA@12345!"
    })
    return r.json()


@pytest.fixture(scope="module")
def user_b():
    """Register User B and return tokens."""
    requests.post(f"{AUTH_URL}/register", json={
        "email": "user_b@sdip.local", "password": "UserB@12345!", "display_name": "User B"
    })
    r = requests.post(f"{AUTH_URL}/login", json={
        "email": "user_b@sdip.local", "password": "UserB@12345!"
    })
    return r.json()


# ─── Attack 1: Brute-Force Login ─────────────────
class TestBruteForceProtection:
    """Verify account lockout after 5 failed login attempts."""

    def test_account_lockout_after_5_failures(self):
        email = f"brute_test_{int(time.time())}@sdip.local"
        # Register target account
        requests.post(f"{AUTH_URL}/register", json={
            "email": email, "password": "BruteTest@1234!", "display_name": "Brute Test"
        })

        # 5 failed attempts
        for i in range(5):
            r = requests.post(f"{AUTH_URL}/login", json={
                "email": email, "password": f"WrongPass{i}!"
            })
            assert r.status_code in (401, 423), f"Attempt {i+1}: expected 401/423, got {r.status_code}"

        # 6th attempt should be locked
        r = requests.post(f"{AUTH_URL}/login", json={
            "email": email, "password": "WrongPass999!"
        })
        assert r.status_code == 423, f"Expected 423 (locked), got {r.status_code}"

    def test_correct_password_after_lockout_still_locked(self):
        email = f"lockout_test_{int(time.time())}@sdip.local"
        password = "LockoutTest@1234!"
        requests.post(f"{AUTH_URL}/register", json={
            "email": email, "password": password, "display_name": "Lockout Test"
        })

        # Trigger lockout
        for _ in range(5):
            requests.post(f"{AUTH_URL}/login", json={
                "email": email, "password": "WrongPass!"
            })

        # Correct password should still fail during lockout
        r = requests.post(f"{AUTH_URL}/login", json={
            "email": email, "password": password
        })
        assert r.status_code == 423


# ─── Attack 2: Invalid/Expired JWT ───────────────
class TestTokenSecurity:
    """Verify that tampered and expired tokens are rejected."""

    def test_missing_token_returns_401(self):
        r = requests.get(f"{DOC_URL}/")
        assert r.status_code == 401

    def test_tampered_token_returns_401(self, user_a):
        token = user_a.get("access_token", "")
        if len(token) > 10:
            tampered = token[:-5] + "XXXXX"
            r = requests.get(f"{DOC_URL}/",
                             headers={"Authorization": f"Bearer {tampered}"})
            assert r.status_code == 401

    def test_random_string_token_returns_401(self):
        r = requests.get(f"{DOC_URL}/",
                         headers={"Authorization": "Bearer totally.fake.token"})
        assert r.status_code == 401


# ─── Attack 3: Unauthorized Document Access ──────
class TestUnauthorizedAccess:
    """Verify users cannot access other users' documents."""

    def test_user_cannot_access_other_user_document(self, user_a, user_b):
        # User A uploads a document
        token_a = user_a.get("access_token")
        r = requests.post(f"{DOC_URL}/upload",
                          headers={"Authorization": f"Bearer {token_a}"},
                          files={"file": ("test.txt", b"Secret document content", "text/plain")},
                          data={"title": "Secret Doc"})
        if r.status_code not in (200, 201):
            pytest.skip("Upload failed, skipping access test")

        doc_id = r.json().get("id")

        # User B tries to access it
        token_b = user_b.get("access_token")
        r = requests.get(f"{DOC_URL}/{doc_id}",
                         headers={"Authorization": f"Bearer {token_b}"})
        assert r.status_code == 403


# ─── Attack 4: Malicious File Upload ─────────────
class TestFileUploadSecurity:
    """Verify malicious file uploads are blocked."""

    def test_rejects_executable_disguised_as_pdf(self, user_a):
        token = user_a.get("access_token")
        # MZ header = Windows executable
        exe_content = b'\x4d\x5a' + b'\x90' * 100
        r = requests.post(f"{DOC_URL}/upload",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"file": ("report.pdf", exe_content, "application/pdf")},
                          data={"title": "Malicious PDF"})
        assert r.status_code == 422

    def test_rejects_disallowed_extension(self, user_a):
        token = user_a.get("access_token")
        r = requests.post(f"{DOC_URL}/upload",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"file": ("malware.exe", b"fake content", "application/octet-stream")},
                          data={"title": "Executable"})
        assert r.status_code == 422

    def test_rejects_oversized_file(self, user_a):
        """Service enforces upload size limit."""
        token = user_a.get("access_token")
        # Generate ~51MB of data (exceeds MAX_UPLOAD_SIZE=50MB)
        large_content = b'x' * (51 * 1024 * 1024)
        r = requests.post(f"{DOC_URL}/upload",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"file": ("big.txt", large_content, "text/plain")},
                          data={"title": "Big File"})
        assert r.status_code in (413, 422)


# ─── Attack 5: SQL Injection ─────────────────────
class TestSQLInjection:
    """Verify parameterized queries prevent SQL injection."""

    def test_injection_in_login_treated_as_literal(self):
        """SQL injection in login password field should not cause server error."""
        r = requests.post(f"{AUTH_URL}/login", json={
            "email": "sqli@test.com",
            "password": "'; DROP TABLE users; --"
        })
        # Should fail with 401 (invalid creds), NOT 500 (SQL error)
        assert r.status_code in (401, 400)

    def test_injection_in_registration(self):
        r = requests.post(f"{AUTH_URL}/register", json={
            "email": "test@test.com",
            "password": "'; DROP TABLE users; --",
            "display_name": "Hacker"
        })
        # Should fail validation (weak password), not SQL error
        assert r.status_code == 400


# ─── Attack 6: Rate Limiting (via Nginx) ─────────
class TestRateLimiting:
    """Verify Nginx rate limiting is enforced (requires Nginx running)."""

    def _nginx_available(self):
        try:
            requests.get(f"{NGINX_URL}/../health", verify=VERIFY_SSL, timeout=2)
            return True
        except Exception:
            return False

    def test_auth_rate_limit_enforced(self):
        """Auth endpoints limited to 5 req/min with burst=3."""
        if not self._nginx_available():
            pytest.skip("Nginx not running - skipping rate limit test")

        responses = []
        for _ in range(10):
            r = requests.post(f"{NGINX_URL}/auth/login", json={
                "email": "nonexistent@test.com", "password": "test"
            }, verify=VERIFY_SSL)
            responses.append(r.status_code)

        # At least some responses should be 429 (rate limited)
        assert 429 in responses, f"Expected 429 in responses: {responses}"


# ─── Attack 7: RBAC Enforcement ──────────────────
class TestRBACEnforcement:
    """Verify non-admin users cannot access admin endpoints."""

    def test_user_cannot_list_all_users(self, user_a):
        token = user_a.get("access_token")
        r = requests.get(f"{AUTH_URL}/users",
                         headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    def test_user_cannot_access_audit_logs(self, user_a):
        token = user_a.get("access_token")
        r = requests.get(f"{AUDIT_URL}/logs",
                         headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403

    def test_user_cannot_view_admin_documents(self, user_a):
        token = user_a.get("access_token")
        r = requests.get(f"{DOC_URL}/admin/all",
                         headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403
