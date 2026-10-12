from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class CsrfTestClient(TestClient):
    def request(self, method: str, url: str, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and "x-csrf-token" not in headers:
            csrf = self.cookies.get("scriptwriting_csrf")
            if csrf:
                headers["x-csrf-token"] = csrf
        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


def client_for(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}",
        cookie_secure=False,
        bootstrap_admin_username=None,
        bootstrap_admin_password=None,
    )
    return CsrfTestClient(create_app(settings))


def login_admin(client: TestClient, password: str = "Admin-password-123") -> None:
    login = client.post("/api/v1/auth/login", json={"username": "admin", "password": password})
    assert login.status_code == 200
    if login.json()["must_change_password"]:
        changed = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": password, "new_password": "Admin-password-456"},
        )
        assert changed.status_code == 200


def test_bootstrap_login_and_first_password_change(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        bootstrap = client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"})
        assert bootstrap.status_code == 201
        assert "password_hash" not in bootstrap.text

        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin-password-123"})
        assert login.status_code == 200
        assert login.json()["must_change_password"] is True
        assert "scriptwriting_session" in client.cookies
        session_token = client.cookies.get("scriptwriting_session")
        assert client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "Admin-password-123", "new_password": "Admin-password-456"},
        ).status_code == 200

        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200
        assert me.json()["username"] == "admin"
        audit = client.get("/api/v1/admin/audit")
        assert audit.status_code == 200
        assert "Admin-password-123" not in audit.text
        assert session_token not in audit.text


def test_new_user_is_forced_to_change_password(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"})
        login_admin(client)
        created = client.post("/api/v1/admin/users", json={"username": "writer", "password": "Writer-password-123"})
        assert created.status_code == 201
        client.post("/api/v1/auth/logout")

        login = client.post("/api/v1/auth/login", json={"username": "writer", "password": "Writer-password-123"})
        assert login.status_code == 200
        assert login.json()["must_change_password"] is True
        assert client.get("/api/v1/admin/users").status_code == 403

        changed = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "Writer-password-123", "new_password": "Writer-password-456"},
        )
        assert changed.status_code == 200
        assert changed.json()["must_change_password"] is False


def test_disable_and_reset_revoke_sessions(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"})
        login_admin(client)
        created = client.post("/api/v1/admin/users", json={"username": "writer", "password": "Writer-password-123"}).json()
        client.post("/api/v1/auth/logout")

        client.post("/api/v1/auth/login", json={"username": "writer", "password": "Writer-password-123"})
        old_cookie = client.cookies.get("scriptwriting_session")
        client.post("/api/v1/auth/logout")

        login_admin(client, password="Admin-password-456")
        assert client.patch(f"/api/v1/admin/users/{created['id']}/status", json={"active": False}).status_code == 200
        client.cookies.set("scriptwriting_session", old_cookie)
        assert client.get("/api/v1/auth/me").status_code == 401

        client.cookies.clear()
        login_admin(client, password="Admin-password-456")
        assert client.patch(f"/api/v1/admin/users/{created['id']}/status", json={"active": True}).status_code == 200
        assert client.post(f"/api/v1/admin/users/{created['id']}/reset-password", json={"password": "Writer-password-456"}).status_code == 200
        assert client.post("/api/v1/auth/login", json={"username": "writer", "password": "Writer-password-123"}).status_code == 401
        assert client.post("/api/v1/auth/login", json={"username": "writer", "password": "Writer-password-456"}).status_code == 200


def test_admin_can_bootstrap_from_environment(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'env.db'}",
        bootstrap_admin_username="env-admin",
        bootstrap_admin_password="Env-password-123",
    )
    with CsrfTestClient(create_app(settings)) as client:
        login = client.post("/api/v1/auth/login", json={"username": "env-admin", "password": "Env-password-123"})
        assert login.status_code == 200
        assert login.json()["must_change_password"] is True


def test_in_memory_database_is_shared_with_test_client() -> None:
    settings = Settings(database_url="sqlite:///:memory:", cookie_secure=False)
    with CsrfTestClient(create_app(settings)) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        assert client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin-password-123"}).status_code == 200


def test_session_cookie_is_http_only_and_same_site(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'cookie.db'}", cookie_secure=True)
    with CsrfTestClient(create_app(settings)) as client:
        client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"})
        login = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Admin-password-123"})
        cookie = next(value for value in login.headers.get_list("set-cookie") if "scriptwriting_session=" in value)
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert "Secure" in cookie


def test_unauthenticated_and_cross_origin_mutations_are_rejected(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.get("/api/v1/auth/me").status_code == 401
        assert client.post("/api/v1/admin/users", json={"username": "blocked", "password": "Blocked-password-123"}).status_code == 401
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        login_admin(client)
        blocked = client.post(
            "/api/v1/admin/users",
            json={"username": "blocked", "password": "Blocked-password-123"},
            headers={"Origin": "https://attacker.example"},
        )
        assert blocked.status_code == 403


def test_failed_logins_are_rate_limited(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        for _ in range(5):
            assert client.post("/api/v1/auth/login", json={"username": "unknown", "password": "wrong"}).status_code == 401
        assert client.post("/api/v1/auth/login", json={"username": "unknown", "password": "wrong"}).status_code == 429

