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


def login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    if response.json()["must_change_password"]:
        changed_password = f"{password}-changed"
        changed = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": password, "new_password": changed_password},
        )
        assert changed.status_code == 200, changed.text
        return changed_password
    return password


def bootstrap_and_login(client: TestClient) -> str:
    response = client.post(
        "/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}
    )
    assert response.status_code == 201, response.text
    return login(client, "admin", "Admin-password-123")


def test_builtin_templates_are_published_and_work_snapshots_template_version(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        admin_password = bootstrap_and_login(client)
        templates = client.get("/api/v1/templates")
        assert templates.status_code == 200
        assert {item["slug"] for item in templates.json()} == {"vertical-micro-drama", "web-short-drama"}
        assert all(item["status"] == "PUBLISHED" for item in templates.json())

        writer = client.post(
            "/api/v1/admin/users", json={"username": "writer", "password": "Writer-password-123"}
        )
        assert writer.status_code == 201, writer.text
        writer_password = login(client, "writer", "Writer-password-123")
        template = next(item for item in templates.json() if item["slug"] == "vertical-micro-drama")
        created = client.post(
            "/api/v1/works",
            json={
                "title": "Template snapshot",
                "template_id": template["id"],
                "settings": {"episode_count": 8, "output_format": "markdown"},
            },
        )
        assert created.status_code == 201, created.text
        work = created.json()
        assert work["template_version_id"] == template["published_version"]["id"]

        client.post("/api/v1/auth/logout")
        login(client, "admin", admin_password)
        changed = client.patch(
            f"/api/v1/admin/templates/{template['id']}",
            json={"definition": {"settings": {"episode_count": {"type": "integer", "min": 1, "max": 20}}}},
        )
        assert changed.status_code == 200, changed.text
        published = client.post(f"/api/v1/admin/templates/{template['id']}/publish")
        assert published.status_code == 200, published.text
        assert published.json()["version"] == template["published_version"]["version"] + 1

        client.post("/api/v1/auth/logout")
        login(client, "writer", writer_password)
        newer = client.post(
            "/api/v1/works",
            json={"title": "Latest template", "template_id": template["id"], "settings": {"episode_count": 4}},
        )
        assert newer.status_code == 201, newer.text
        assert newer.json()["template_version_id"] == published.json()["id"]
        fetched = client.get(f"/api/v1/works/{work['id']}")
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["template_version_id"] == work["template_version_id"]
        assert fetched.json()["settings"] == {"episode_count": 8, "output_format": "markdown"}


def test_template_schema_rejects_unknown_settings_and_editor_cannot_change_work_settings(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        bootstrap_and_login(client)
        editor = client.post(
            "/api/v1/admin/users", json={"username": "editor", "password": "Editor-password-123"}
        ).json()
        custom = client.post(
            "/api/v1/admin/templates",
            json={
                "slug": "custom",
                "name": "自定义模板",
                "definition": {
                    "settings": {"episode_count": {"type": "integer", "min": 1, "max": 10}},
                    "creative_brief_fields": ["premise"],
                    "character_fields": ["name"],
                    "outline_fields": ["beats"],
                    "script_structure": {"blocks": ["SCENE_HEADING", "ACTION"]},
                    "output_formats": ["markdown"],
                },
            },
        )
        assert custom.status_code == 201, custom.text
        template_id = custom.json()["id"]
        assert client.post(f"/api/v1/admin/templates/{template_id}/publish").status_code == 200
        login(client, "admin", "Admin-password-123-changed")
        work = client.post(
            "/api/v1/works", json={"title": "Validated", "template_id": template_id, "settings": {"episode_count": 4}}
        )
        assert work.status_code == 201, work.text
        work_id = work.json()["id"]
        invalid = client.patch(f"/api/v1/works/{work_id}/settings", json={"unknown": True})
        assert invalid.status_code == 422
        assert invalid.json()["detail"]["code"] == "INVALID_TEMPLATE_FIELD"
        merged = client.patch(f"/api/v1/works/{work_id}/settings", json={"episode_count": 5})
        assert merged.status_code == 200, merged.text
        assert merged.json()["settings"] == {"episode_count": 5}
        assert client.post(
            f"/api/v1/works/{work_id}/members/invitations", json={"user_id": editor["id"], "role": "EDITOR"}
        ).status_code == 201
        client.post("/api/v1/auth/logout")
        login(client, "editor", "Editor-password-123")
        assert client.post(f"/api/v1/works/{work_id}/members/accept").status_code == 200
        denied = client.patch(f"/api/v1/works/{work_id}/settings", json={"episode_count": 5})
        assert denied.status_code == 403


def test_creative_stages_are_ordered_and_roles_are_enforced(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        bootstrap_and_login(client)
        editor = client.post(
            "/api/v1/admin/users", json={"username": "editor", "password": "Editor-password-123"}
        ).json()
        viewer = client.post(
            "/api/v1/admin/users", json={"username": "viewer", "password": "Viewer-password-123"}
        ).json()
        client.post("/api/v1/auth/logout")
        writer_password = login(client, "admin", "Admin-password-123-changed")
        work = client.post(
            "/api/v1/works", json={"title": "Stages", "settings": {"episode_count": 6}}
        ).json()
        work_id = work["id"]
        assert client.post(
            f"/api/v1/works/{work_id}/members/invitations", json={"user_id": editor["id"], "role": "EDITOR"}
        ).status_code == 201
        assert client.post(
            f"/api/v1/works/{work_id}/members/invitations", json={"user_id": viewer["id"], "role": "VIEWER"}
        ).status_code == 201

        client.post("/api/v1/auth/logout")
        editor_password = login(client, "editor", "Editor-password-123")
        assert client.post(f"/api/v1/works/{work_id}/members/accept").status_code == 200
        assert client.patch(
            f"/api/v1/works/{work_id}/creative/brief", json={"theme": "救赎", "tone": "紧张"}
        ).status_code == 200
        assert client.patch(
            f"/api/v1/works/{work_id}/creative/characters", json=[{"name": "林岚", "role": "记者"}]
        ).status_code == 200
        assert client.patch(
            f"/api/v1/works/{work_id}/creative/outline", json={"premise": "寻找真相"}
        ).status_code == 200
        assert client.post(f"/api/v1/works/{work_id}/creative/outline/confirm").status_code == 403
        client.post("/api/v1/auth/logout")
        owner_password = login(client, "admin", writer_password)
        assert client.post(f"/api/v1/works/{work_id}/creative/brief/confirm").status_code == 200
        blocked = client.post(f"/api/v1/works/{work_id}/creative/outline/confirm")
        assert blocked.status_code == 409
        assert blocked.json()["detail"]["code"] == "STAGE_ORDER_INVALID"
        assert client.post(f"/api/v1/works/{work_id}/creative/characters/confirm").status_code == 200
        assert client.post(f"/api/v1/works/{work_id}/creative/outline/confirm").status_code == 200

        client.post("/api/v1/auth/logout")
        login(client, "viewer", "Viewer-password-123")
        assert client.post(f"/api/v1/works/{work_id}/members/accept").status_code == 200
        assert client.get(f"/api/v1/works/{work_id}/creative").status_code == 200
        assert client.patch(f"/api/v1/works/{work_id}/creative/brief", json={"theme": "越权"}).status_code == 403
        client.post("/api/v1/auth/logout")
        login(client, "admin", owner_password)


def test_confirmed_setting_changes_require_a_single_non_stale_proposal(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        bootstrap_and_login(client)
        editor = client.post(
            "/api/v1/admin/users", json={"username": "editor", "password": "Editor-password-123"}
        ).json()
        work = client.post(
            "/api/v1/works", json={"title": "Proposals", "settings": {"episode_count": 6}}
        ).json()
        work_id = work["id"]
        assert client.post(
            f"/api/v1/works/{work_id}/members/invitations", json={"user_id": editor["id"], "role": "EDITOR"}
        ).status_code == 201
        client.post("/api/v1/auth/logout")
        login(client, "editor", "Editor-password-123")
        assert client.post(f"/api/v1/works/{work_id}/members/accept").status_code == 200
        assert client.patch(f"/api/v1/works/{work_id}/creative/brief", json={"theme": "原主题"}).status_code == 200
        client.post("/api/v1/auth/logout")
        login(client, "admin", "Admin-password-123-changed")
        assert client.post(f"/api/v1/works/{work_id}/creative/brief/confirm").status_code == 200

        client.post("/api/v1/auth/logout")
        login(client, "editor", "Editor-password-123-changed")
        assert client.patch(f"/api/v1/works/{work_id}/creative/brief", json={"theme": "新主题"}).status_code == 200
        proposal = client.post(
            f"/api/v1/works/{work_id}/change-proposals", json={"stage": "brief", "content": {"theme": "提案主题"}}
        )
        assert proposal.status_code == 201, proposal.text
        duplicate = client.post(
            f"/api/v1/works/{work_id}/change-proposals", json={"stage": "brief", "content": {"theme": "另一个"}}
        )
        assert duplicate.status_code == 409
        proposal_id = proposal.json()["id"]
        assert client.patch(f"/api/v1/works/{work_id}/creative/brief", json={"theme": "基础已变化"}).status_code == 200

        client.post("/api/v1/auth/logout")
        login(client, "admin", "Admin-password-123-changed")
        stale = client.post(f"/api/v1/works/{work_id}/change-proposals/{proposal_id}/approve")
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "PROPOSAL_BASE_CHANGED"
        assert stale.json()["detail"]["message"]
        replacement = client.post(
            f"/api/v1/works/{work_id}/change-proposals", json={"stage": "brief", "content": {"theme": "重新提交"}}
        )
        assert replacement.status_code == 201, replacement.text
        assert client.get(f"/api/v1/works/{work_id}/change-proposals").status_code == 200
