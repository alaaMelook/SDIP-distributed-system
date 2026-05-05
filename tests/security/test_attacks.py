"""
SDIP Security Attack Simulation Tests
Run: pytest tests/security/ -v --disable-warnings
Requires the system to be running via docker compose up
"""
import pytest
import requests
import time

BASE_URL = "https://localhost/api"
VERIFY_SSL = False  # Self-signed cert in dev


# ─── Fixtures ────────────────────────────────────
@pytest.fixture(scope="module")
def admin_token():
    """Register and login an admin user for testing."""
    # Note: In real tests, use the create-admin script first
    r = requests.post(f"{BASE_URL}/auth/register", json={
        "email": "admin_test@sdip.local",
        "password": "Admin@12345!",
        "display_name": "Test Admin"
    }, verify=VERIFY_SSL)
    if r.status_code in (201, 409):  # Created or already exists
        r = requests.post(f"{BASE_URL}/auth/login", json={
            "email": "admin_test@sdip.local",
            "password": "Admin@12345!"
        }, verify=VERIFY_SSL)
        return r.json().get("access_token")
    pytest.skip("Could not create admin user")


@pytest.fixture(scope="module")
def user_a():
    """Register User A and return tokens."""
    requests.post(f"{BASE_URL}/auth/register", json={
        "email": "user_a@sdip.local", "password": "UserA@12345!", "display_name": "User A"
    }, verify=VERIFY_SSL)
    r = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "user_a@sdip.local", "password": "UserA@12345!"
    }, verify=VERIFY_SSL)
    return r.json()


@pytest.fixture(scope="module")
def user_b():
    """Register User B and return tokens."""
    requests.post(f"{BASE_URL}/auth/register", json={
        "email": "user_b@sdip.local", "password": "UserB@12345!", "display_name": "User B"
    }, verify=VERIFY_SSL)
    r = requests.post(f"{BASE_URL}/auth/login", json={
        "email": "user_b@sdip.local", "password": "UserB@12345!"
    }, verify=VERIFY_SSL)
    return r.json()


# ─── Attack 1: Brute-Force Login ─────────────────
class TestBruteForceProtection:
    """Verify account lockout after 5 failed login attempts."""

    def test_account_lockout_after_5_failures(self):
        email = f"brute_test_{int(time.time())}@sdip.local"
        # Register target account
        requests.post(f"{BASE_URL}/auth/register", json={
            "email": email, "password": "BruteTest@1234!", "display_name": "Brute Test"
        }, verify=VERIFY_SSL)

        # 5 failed attempts
        for i in range(5):
            r = requests.post(f"{BASE_URL}/auth/login", json={
                "email": email, "password": f"WrongPass{i}!"
            }, verify=VERIFY_SSL)
            assert r.status_code in (401, 423), f"Attempt {i+1}: expected 401/423, got {r.status_code}"

        # 6th attempt should be locked
        r = requests.post(f"{BASE_URL}/auth/login", json={
            "email": email, "password": "WrongPass999!"
        }, verify=VERIFY_SSL)
        assert r.status_code == 423, f"Expected 423 (locked), got {r.status_code}"

    def test_correct_password_after_lockout_still_locked(self):
        email = f"lockout_test_{int(time.time())}@sdip.local"
        password = "LockoutTest@1234!"
        requests.post(f"{BASE_URL}/auth/register", json={
            "email": email, "password": password, "display_name": "Lockout Test"
        }, verify=VERIFY_SSL)

        # Trigger lockout
        for _ in range(5):
            requests.post(f"{BASE_URL}/auth/login", json={
                "email": email, "password": "WrongPass!"
            }, verify=VERIFY_SSL)

        # Correct password should still fail during lockout
        r = requests.post(f"{BASE_URL}/auth/login", json={
            "email": email, "password": password
        }, verify=VERIFY_SSL)
        assert r.status_code == 423


# ─── Attack 2: Invalid/Expired JWT ───────────────
class TestTokenSecurity:
    """Verify that tampered and expired tokens are rejected."""

    def test_missing_token_returns_401(self):
        r = requests.get(f"{BASE_URL}/documents/", verify=VERIFY_SSL)
        assert r.status_code == 401

    def test_tampered_token_returns_401(self, user_a):
        token = user_a.get("access_token", "")
        if len(token) > 10:
            tampered = token[:-5] + "XXXXX"
            r = requests.get(f"{BASE_URL}/documents/",
                             headers={"Authorization": f"Bearer {tampered}"}, verify=VERIFY_SSL)
            assert r.status_code == 401

    def test_random_string_token_returns_401(self):
        r = requests.get(f"{BASE_URL}/documents/",
                         headers={"Authorization": "Bearer totally.fake.token"}, verify=VERIFY_SSL)
        assert r.status_code == 401


# ─── Attack 3: Unauthorized Document Access ──────
class TestUnauthorizedAccess:
    """Verify users cannot access other users' documents."""

    def test_user_cannot_access_other_user_document(self, user_a, user_b):
        # User A uploads a document
        token_a = user_a.get("access_token")
        r = requests.post(f"{BASE_URL}/documents/upload",
                          headers={"Authorization": f"Bearer {token_a}"},
                          files={"file": ("test.txt", b"Secret document content", "text/plain")},
                          data={"title": "Secret Doc"},
                          verify=VERIFY_SSL)
        if r.status_code != 200:
            pytest.skip("Upload failed, skipping access test")

        doc_id = r.json().get("id")

        # User B tries to access it
        token_b = user_b.get("access_token")
        r = requests.get(f"{BASE_URL}/documents/{doc_id}",
                         headers={"Authorization": f"Bearer {token_b}"}, verify=VERIFY_SSL)
        assert r.status_code == 403


# ─── Attack 4: Malicious File Upload ─────────────
class TestFileUploadSecurity:
    """Verify malicious file uploads are blocked."""

    def test_rejects_executable_disguised_as_pdf(self, user_a):
        token = user_a.get("access_token")
        # MZ header = Windows executable
        exe_content = b'\x4d\x5a' + b'\x90' * 100
        r = requests.post(f"{BASE_URL}/documents/upload",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"file": ("report.pdf", exe_content, "application/pdf")},
                          data={"title": "Malicious PDF"},
                          verify=VERIFY_SSL)
        assert r.status_code == 422

    def test_rejects_disallowed_extension(self, user_a):
        token = user_a.get("access_token")
        r = requests.post(f"{BASE_URL}/documents/upload",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"file": ("malware.exe", b"fake content", "application/octet-stream")},
                          data={"title": "Executable"},
                          verify=VERIFY_SSL)
        assert r.status_code == 422

    def test_rejects_oversized_file_at_nginx(self, user_a):
        """Nginx enforces 50MB limit before request reaches the app."""
        token = user_a.get("access_token")
        # Generate 51MB of data
        large_content = b'x' * (51 * 1024 * 1024)
        r = requests.post(f"{BASE_URL}/documents/upload",
                          headers={"Authorization": f"Bearer {token}"},
                          files={"file": ("big.txt", large_content, "text/plain")},
                          data={"title": "Big File"},
                          verify=VERIFY_SSL)
        assert r.status_code == 413


# ─── Attack 5: SQL Injection ─────────────────────
class TestSQLInjection:
    """Verify parameterized queries prevent SQL injection."""

    def test_injection_in_search_treated_as_literal(self, user_a):
        token = user_a.get("access_token")
        r = requests.post(f"{BASE_URL}/search/fulltext",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"query": "'; DROP TABLE documents; --"},
                          verify=VERIFY_SSL)
        # Should succeed (treated as literal search) or return empty results, NOT error
        assert r.status_code in (200, 404)

    def test_injection_in_registration(self):
        r = requests.post(f"{BASE_URL}/auth/register", json={
            "email": "test@test.com",
            "password": "'; DROP TABLE users; --",
            "display_name": "Hacker"
        }, verify=VERIFY_SSL)
        # Should fail validation (weak password), not SQL error
        assert r.status_code == 400


# ─── Attack 6: Rate Limiting ─────────────────────
class TestRateLimiting:
    """Verify Nginx rate limiting is enforced."""

    def test_auth_rate_limit_enforced(self):
        """Auth endpoints limited to 5 req/min with burst=3."""
        responses = []
        for _ in range(10):
            r = requests.post(f"{BASE_URL}/auth/login", json={
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
        r = requests.get(f"{BASE_URL}/auth/users",
                         headers={"Authorization": f"Bearer {token}"}, verify=VERIFY_SSL)
        assert r.status_code == 403

    def test_user_cannot_access_audit_logs(self, user_a):
        token = user_a.get("access_token")
        r = requests.get(f"{BASE_URL}/audit/logs",
                         headers={"Authorization": f"Bearer {token}"}, verify=VERIFY_SSL)
        assert r.status_code == 403

    def test_user_cannot_view_admin_documents(self, user_a):
        token = user_a.get("access_token")
        r = requests.get(f"{BASE_URL}/documents/admin/all",
                         headers={"Authorization": f"Bearer {token}"}, verify=VERIFY_SSL)
        assert r.status_code == 403
