from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_check_reports_ready() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_built_frontend_and_api_share_one_origin(tmp_path: Path) -> None:
    from app.config import Settings

    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text('<html><title>幕间</title></html>', encoding="utf-8")
    (web / "assets").mkdir()
    (web / "assets" / "app.js").write_text('console.log("studio")', encoding="utf-8")
    with TestClient(create_app(Settings(database_url=f"sqlite:///{tmp_path / 'static.db'}", frontend_dist=str(web), bootstrap_admin_username=None, bootstrap_admin_password=None))) as client:
        assert "幕间" in client.get("/").text
        assert client.get("/assets/app.js").status_code == 200
        assert client.get("/healthz").json() == {"status": "ok"}
        assert client.get("/api/v1/works").status_code == 401
        assert client.get("/api/v1/unknown").status_code == 404
