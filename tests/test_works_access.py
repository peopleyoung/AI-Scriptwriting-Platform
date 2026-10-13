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


def login(client: TestClient, username: str, password: str) -> None:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200
    if response.json()["must_change_password"]:
        changed = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": password, "new_password": f"{password}-changed"},
        )
        assert changed.status_code == 200


def test_work_is_private_to_creator(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.post(
            "/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}
        ).status_code == 201
        login(client, "admin", "Admin-password-123")
        writer = client.post(
            "/api/v1/admin/users", json={"username": "writer", "password": "Writer-password-123"}
        )
        assert writer.status_code == 201
        writer_id = writer.json()["id"]
        client.post("/api/v1/auth/logout")
        login(client, "writer", "Writer-password-123")

        created = client.post("/api/v1/works", json={"title": "Private pilot"})
        assert created.status_code == 201
        work_id = created.json()["id"]
        assert created.json()["owner_id"] == writer_id
        assert created.json()["status"] == "ACTIVE"
        assert [work["id"] for work in client.get("/api/v1/works").json()] == [work_id]

        client.post("/api/v1/auth/logout")
        login(client, "admin", "Admin-password-123-changed")
        assert client.get(f"/api/v1/works/{work_id}").status_code == 403


def test_invitation_requires_acceptance_and_revoke_is_immediate(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.post(
            "/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}
        ).status_code == 201
        login(client, "admin", "Admin-password-123")
        editor = client.post(
            "/api/v1/admin/users", json={"username": "editor", "password": "Editor-password-123"}
        ).json()
        created = client.post("/api/v1/works", json={"title": "Shared pilot"}).json()
        work_id = created["id"]

        invitation = client.post(
            f"/api/v1/works/{work_id}/members/invitations",
            json={"username": "editor", "role": "EDITOR"},
        )
        assert invitation.status_code == 201
        assert invitation.json()["status"] == "PENDING"

        client.post("/api/v1/auth/logout")
        login(client, "editor", "Editor-password-123")
        assert client.get("/api/v1/works").json() == []
        assert client.get(f"/api/v1/works/{work_id}").status_code == 403
        accepted = client.post(f"/api/v1/works/{work_id}/members/accept")
        assert accepted.status_code == 200
        assert accepted.json()["role"] == "EDITOR"
        assert client.get(f"/api/v1/works/{work_id}").status_code == 200

        client.post("/api/v1/auth/logout")
        login(client, "admin", "Admin-password-123-changed")
        revoked = client.post(f"/api/v1/works/{work_id}/members/{editor['id']}/revoke")
        assert revoked.status_code == 200
        assert revoked.json()["status"] == "REVOKED"
        client.post("/api/v1/auth/logout")
        login(client, "editor", "Editor-password-123-changed")
        assert client.get(f"/api/v1/works/{work_id}").status_code == 403


def test_owner_can_transfer_and_trash_hides_work_until_restore(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.post(
            "/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}
        ).status_code == 201
        login(client, "admin", "Admin-password-123")
        editor = client.post(
            "/api/v1/admin/users", json={"username": "editor", "password": "Editor-password-123"}
        ).json()
        work_id = client.post("/api/v1/works", json={"title": "Transferable pilot"}).json()["id"]
        assert client.post(
            f"/api/v1/works/{work_id}/members/invitations",
            json={"user_id": editor["id"], "role": "EDITOR"},
        ).status_code == 201

        client.post("/api/v1/auth/logout")
        login(client, "editor", "Editor-password-123")
        assert client.post(f"/api/v1/works/{work_id}/members/accept").status_code == 200
        client.post("/api/v1/auth/logout")
        login(client, "admin", "Admin-password-123-changed")

        transferred = client.post(
            f"/api/v1/works/{work_id}/transfer-ownership", json={"user_id": editor["id"]}
        )
        assert transferred.status_code == 200
        assert transferred.json()["owner_id"] == editor["id"]
        assert client.post(f"/api/v1/works/{work_id}/trash").status_code == 403
        assert client.post(
            f"/api/v1/works/{work_id}/members/invitations",
            json={"username": "admin", "role": "VIEWER"},
        ).status_code == 403
        assert client.post(f"/api/v1/works/{work_id}/transfer-ownership", json={"user_id": editor["id"]}).status_code == 403

        client.post("/api/v1/auth/logout")
        login(client, "editor", "Editor-password-123-changed")
        assert client.get(f"/api/v1/works/{work_id}").status_code == 200
        trashed = client.post(f"/api/v1/works/{work_id}/trash")
        assert trashed.status_code == 200
        assert trashed.json()["status"] == "TRASHED"
        assert client.get("/api/v1/works").json() == []
        assert client.get(f"/api/v1/works/{work_id}").status_code == 404
        restored = client.post(f"/api/v1/works/{work_id}/restore")
        assert restored.status_code == 200
        assert restored.json()["status"] == "ACTIVE"
        assert [work["id"] for work in client.get("/api/v1/works").json()] == [work_id]


def test_account_owner_must_transfer_before_disable(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.post(
            "/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}
        ).status_code == 201
        login(client, "admin", "Admin-password-123")
        owner = client.post(
            "/api/v1/admin/users", json={"username": "owner", "password": "Owner-password-123"}
        ).json()
        client.post("/api/v1/auth/logout")
        login(client, "owner", "Owner-password-123")
        assert client.post("/api/v1/works", json={"title": "Owned pilot"}).status_code == 201
        client.post("/api/v1/auth/logout")
        login(client, "admin", "Admin-password-123-changed")
        blocked = client.patch(
            f"/api/v1/admin/users/{owner['id']}/status", json={"active": False}
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "OWNER_TRANSFER_REQUIRED"


def test_only_owner_can_manage_members_and_role_changes_are_audited(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.post(
            "/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}
        ).status_code == 201
        login(client, "admin", "Admin-password-123")
        viewer = client.post(
            "/api/v1/admin/users", json={"username": "viewer", "password": "Viewer-password-123"}
        ).json()
        work_id = client.post("/api/v1/works", json={"title": "Roles pilot"}).json()["id"]
        assert client.post(
            f"/api/v1/works/{work_id}/members/invitations",
            json={"user_id": viewer["id"], "role": "VIEWER"},
        ).status_code == 201

        client.post("/api/v1/auth/logout")
        login(client, "viewer", "Viewer-password-123")
        assert client.post(f"/api/v1/works/{work_id}/members/accept").status_code == 200
        assert client.patch(
            f"/api/v1/works/{work_id}/members/{viewer['id']}", json={"role": "EDITOR"}
        ).status_code == 403

        client.post("/api/v1/auth/logout")
        login(client, "admin", "Admin-password-123-changed")
        changed = client.patch(
            f"/api/v1/works/{work_id}/members/{viewer['id']}", json={"role": "EDITOR"}
        )
        assert changed.status_code == 200
        assert changed.json()["role"] == "EDITOR"
        audit = client.get("/api/v1/admin/audit")
        assert audit.status_code == 200
        assert "WORK_MEMBER_ROLE_CHANGED" in audit.text


def test_frontend_lists_expose_only_authorized_metadata(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        login(client, "admin", "Admin-password-123")
        for username in ("writer", "editor"):
            assert client.post("/api/v1/admin/users", json={"username": username, "password": "Writer-password-123"}).status_code == 201
        client.post("/api/v1/auth/logout")
        login(client, "writer", "Writer-password-123")
        work = client.post("/api/v1/works", json={"title": "私有故事"}).json()
        work_id = work["id"]
        detail = client.get(f"/api/v1/works/{work_id}").json()
        assert detail["template_definition"]["script_structure"]["blocks"]
        assert client.post(f"/api/v1/works/{work_id}/members/invitations", json={"username": "editor", "role": "EDITOR"}).status_code == 201
        assert client.get("/api/v1/invitations").json() == []
        client.post("/api/v1/auth/logout")
        login(client, "editor", "Writer-password-123")
        invitations = client.get("/api/v1/invitations").json()
        assert len(invitations) == 1
        assert set(invitations[0]) == {"work_id", "title", "role", "invited_at"}
        assert invitations[0]["work_id"] == work_id
        assert client.get(f"/api/v1/works/{work_id}/workspace").status_code == 403
        assert client.get("/api/v1/trash").json() == []
        client.post("/api/v1/auth/logout")
        login(client, "writer", "Writer-password-123-changed")
        assert client.post(f"/api/v1/works/{work_id}/trash").status_code == 200
        trash = client.get("/api/v1/trash").json()
        assert [item["id"] for item in trash] == [work_id]
        assert not {"draft", "creative", "members", "template_definition"} & set(trash[0])
        client.post("/api/v1/auth/logout")
        login(client, "editor", "Writer-password-123-changed")
        assert client.get("/api/v1/invitations").json() == []
        assert client.get("/api/v1/trash").json() == []
        client.post("/api/v1/auth/logout")
        login(client, "admin", "Admin-password-123-changed")
        assert client.get("/api/v1/trash").json() == []
        assert client.get(f"/api/v1/works/{work_id}").status_code == 404


def test_confirmation_does_not_accept_an_unseen_draft_revision(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        login(client, "admin", "Admin-password-123")
        work_id = client.post("/api/v1/works", json={"title": "确认边界"}).json()["id"]
        assert client.patch(f"/api/v1/works/{work_id}/creative/brief", json={"theme": "原主题"}).status_code == 200
        assert client.patch(f"/api/v1/works/{work_id}/creative/brief", json={"theme": "协作者的新主题"}).status_code == 200
        stale = client.post(f"/api/v1/works/{work_id}/creative/brief/confirm", headers={"if-match": "1"})
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
        assert client.get(f"/api/v1/works/{work_id}/creative/brief").json()["confirmed"] is None
        accepted = client.post(f"/api/v1/works/{work_id}/creative/brief/confirm", headers={"if-match": "2"})
        assert accepted.status_code == 200
        assert accepted.json()["confirmed"] == {"theme": "协作者的新主题"}
