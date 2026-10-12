from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


class CsrfClient(TestClient):
    def request(self, method: str, url: str, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and "x-csrf-token" not in headers:
            token = self.cookies.get("scriptwriting_csrf")
            if token:
                headers["x-csrf-token"] = token
        kwargs["headers"] = headers
        return super().request(method, url, **kwargs)


def _client(tmp_path: Path) -> CsrfClient:
    return CsrfClient(create_app(Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", cookie_secure=False, backup_dir=str(tmp_path / "backups"))))


def _login(client: TestClient, username: str, password: str) -> str:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    if response.json()["must_change_password"]:
        changed = f"{password}-changed"
        assert client.post("/api/v1/auth/change-password", json={"current_password": password, "new_password": changed}).status_code == 200
        return changed
    return password


def _confirm_all(client: TestClient, work_id: str) -> None:
    assert client.patch(f"/api/v1/works/{work_id}/creative/brief", json={"theme": "主题"}).status_code == 200
    assert client.patch(f"/api/v1/works/{work_id}/creative/characters", json=[{"name": "主角"}]).status_code == 200
    assert client.patch(f"/api/v1/works/{work_id}/creative/outline", json={"premise": "寻找真相"}).status_code == 200
    for stage in ("brief", "characters", "outline"):
        assert client.post(f"/api/v1/works/{work_id}/creative/{stage}/confirm").status_code == 200


def test_versions_are_immutable_and_export_reads_release(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        _login(client, "admin", "Admin-password-123")
        work = client.post("/api/v1/works", json={"title": "版本测试"}).json()
        work_id = work["id"]
        draft = client.patch(f"/api/v1/works/{work_id}/draft", json={"content": {"episodes": []}, "expected_revision": 0})
        assert draft.status_code == 200
        version = client.post(f"/api/v1/works/{work_id}/versions", json={"expected_revision": draft.json()["revision"]})
        assert version.status_code == 201, version.text
        version_id = version.json()["id"]
        compare = client.get(f"/api/v1/works/{work_id}/versions/compare", params={"from_version": version_id, "to_version": version_id})
        assert compare.status_code == 200
        assert compare.json()["changed"] is False
        assert client.post(f"/api/v1/works/{work_id}/releases", json={"version_id": version_id}).status_code == 201
        export = client.post(f"/api/v1/works/{work_id}/exports", json={"format": "markdown"})
        assert export.status_code == 201, export.text
        download = client.get(export.json()["download_url"])
        assert download.status_code == 200
        assert "版本测试" in download.text


def test_generation_requires_consent_and_creates_version(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        _login(client, "admin", "Admin-password-123")
        work_id = client.post("/api/v1/works", json={"title": "生成测试"}).json()["id"]
        _confirm_all(client, work_id)
        provider = client.post("/api/v1/admin/providers", json={"slug": "mock", "name": "Mock", "adapter": "mock", "retention_description": "r", "deletion_description": "d", "incident_description": "i"}).json()
        assert client.patch(f"/api/v1/admin/providers/{provider['id']}", json={"enabled": True}).status_code == 200
        config = client.post("/api/v1/admin/model-configs", json={"provider_id": provider["id"], "model_name": "mock"}).json()
        consent = client.post(f"/api/v1/works/{work_id}/consents", json={"model_config_version_id": config["id"], "data_categories": ["script"]}).json()
        queued = client.post(f"/api/v1/works/{work_id}/generation-jobs", json={"consent_id": consent["id"], "model_config_version_id": config["id"], "idempotency_key": "one", "instruction": "写一个片段"})
        assert queued.status_code == 201
        assert queued.json()["status"] == "QUEUED"
        done = client.post(f"/api/v1/works/{work_id}/generation-jobs/{queued.json()['id']}/run")
        assert done.status_code == 200
        assert done.json()["status"] == "SUCCEEDED"
        assert len(client.get(f"/api/v1/works/{work_id}/versions").json()) == 1
