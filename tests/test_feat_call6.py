"""CALL-6 feature coverage: EDITOR version-save loop, trash retention/expiry,
production config Fail-Fast, and worker restart recovery + retry lineage."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app import lifecycle
from app.config import Settings
from app.models import GenerationJob, Work, WorkAIActivity
from sqlalchemy import select

from test_versions_generation_export import _client, _login, _confirm_all, CsrfClient
from http_model_server import model_server


def _work(client) -> str:
    return client.post("/api/v1/works", json={"title": "作品"}).json()["id"]


def _script(number: int = 1):
    return {"episodes": [{"number": number, "title": "第一集", "scenes": [{"number": 1, "heading": "夜", "fragments": [{"id": f"f{number}", "blocks": [{"type": "ACTION", "text": f"正文{number}"}]}]}]}]}


def _invite_editor(client, work_id: str) -> CsrfClient:
    user = client.post(
        "/api/v1/admin/users",
        json={"username": "editor", "password": "Editor-password-123", "role": "WRITER"},
    ).json()
    assert client.post(
        f"/api/v1/works/{work_id}/members/invitations",
        json={"user_id": user["id"], "role": "EDITOR"},
    ).status_code == 201
    editor = CsrfClient(client.app)
    _login(editor, "editor", "Editor-password-123")
    assert editor.post(f"/api/v1/works/{work_id}/members/accept").status_code == 200
    return editor


def _detail_code(response) -> str:
    detail = response.json()["detail"]
    return detail["code"] if isinstance(detail, dict) else detail


# --- FEAT-01: EDITOR manual version-save permission loop ---
def test_editor_saves_version_but_cannot_restore_publish_export(tmp_path: Path):
    with _client(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        _login(client, "admin", "Admin-password-123")
        work = _work(client)
        client.patch(f"/api/v1/works/{work}/draft", json={"content": _script(0)})
        editor = _invite_editor(client, work)

        saved = editor.post(f"/api/v1/works/{work}/versions", json={"content": _script(1)})
        assert saved.status_code == 201, saved.text
        version_id = saved.json()["id"]
        # The version record carries the editor as author.
        assert saved.json()["author_display_name"] == "editor"

        assert _detail_code(editor.post(f"/api/v1/works/{work}/versions/{version_id}/restore")) == "WORK_ACCESS_DENIED"
        assert _detail_code(editor.post(f"/api/v1/works/{work}/publish", json={"version_id": version_id})) == "WORK_ACCESS_DENIED"
        assert _detail_code(editor.post(f"/api/v1/works/{work}/exports", json={"format": "markdown"})) == "WORK_ACCESS_DENIED"

        # Owner keeps the privileged actions and reads the same author name.
        assert client.post(f"/api/v1/works/{work}/publish", json={"version_id": version_id}).status_code in {200, 201}
        rows = client.get(f"/api/v1/works/{work}/versions").json()
        assert {row["author_display_name"] for row in rows} >= {"editor"}


def test_workspace_permissions_expose_save_and_restore_split(tmp_path: Path):
    with _client(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        _login(client, "admin", "Admin-password-123")
        work = _work(client)
        editor = _invite_editor(client, work)
        perms = editor.get(f"/api/v1/works/{work}/workspace").json()["permissions"]
        assert perms["can_save_version"] is True
        assert perms["can_restore_version"] is False
        assert perms["can_publish"] is False
        assert perms["can_export"] is False


# --- FEAT-02: trash retention, restore 410, purge, runtime-config ---
def test_restore_after_retention_returns_410_and_purge_is_physical(tmp_path: Path):
    with _client(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        _login(client, "admin", "Admin-password-123")
        work = _work(client)
        assert client.post(f"/api/v1/works/{work}/trash").status_code == 200

        # Not yet expired → restorable.
        assert client.post(f"/api/v1/works/{work}/restore").status_code == 200
        assert client.post(f"/api/v1/works/{work}/trash").status_code == 200

        # Backdate beyond the retention window.
        factory = client.app.state.session_factory
        retention = client.app.state.settings.trash_retention_days
        with factory() as db:
            row = db.scalar(select(Work).where(Work.id == work))
            row.trashed_at = datetime.now(timezone.utc) - timedelta(days=retention + 1)
            db.commit()

        expired = client.post(f"/api/v1/works/{work}/restore")
        assert expired.status_code == 410
        assert _detail_code(expired) == "TRASH_RETENTION_EXPIRED"

        purged = lifecycle.purge_expired_works(factory, retention)
        assert purged == 1
        with factory() as db:
            assert db.scalar(select(Work).where(Work.id == work)) is None
            assert db.scalar(select(WorkAIActivity).where(WorkAIActivity.work_id == work)) is None


def test_expired_boundary_is_inclusive():
    trashed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    deadline = trashed + timedelta(days=30)
    assert lifecycle.is_expired(trashed, 30, deadline - timedelta(seconds=1)) is False
    assert lifecycle.is_expired(trashed, 30, deadline) is True
    assert lifecycle.is_expired(trashed, 30, deadline + timedelta(seconds=1)) is True


def test_runtime_config_reports_retention_days(tmp_path: Path):
    with _client(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        _login(client, "admin", "Admin-password-123")
        body = client.get("/api/v1/runtime-config").json()
        assert body == {"trash_retention_days": client.app.state.settings.trash_retention_days}


# --- FEAT-03: production config Fail-Fast ---
def test_production_rejects_weak_or_placeholder_secrets():
    with pytest.raises(ValidationError) as key_err:
        Settings(environment="production", external_encryption_key="development-only-key")
    assert "APP_EXTERNAL_ENCRYPTION_KEY" in str(key_err.value)

    with pytest.raises(ValidationError):
        Settings(environment="production", external_encryption_key="x" * 40, bootstrap_admin_password="password")

    with pytest.raises(ValidationError):
        Settings(environment="production", external_encryption_key="x" * 40, bootstrap_secret="change-this-setup-secret")


def test_production_errors_do_not_leak_secret_values():
    token = "dontlogthisSECRETtoken1234567890"
    with pytest.raises(ValidationError) as err:
        Settings(
            environment="production",
            external_encryption_key="x" * 40,
            bootstrap_secret=f"change-this-{token}",
        )
    assert token not in str(err.value)


def test_development_and_test_allow_placeholder_values():
    # Fail-Fast is scoped to production; local/CI boot without real secrets.
    dev = Settings(environment="development")
    assert dev.external_encryption_key == "development-only-key"
    assert Settings(environment="development", bootstrap_admin_password="password").bootstrap_admin_password == "password"


def _example_env_values() -> dict[str, str]:
    path = Path(__file__).resolve().parents[1] / ".env.example"
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


# The shipped .env.example placeholders are long enough to pass a naive length
# check; pinning the validator to the actual file values is what keeps the
# example and the Fail-Fast rules from drifting apart again.
@pytest.mark.parametrize(
    "field, valid_others",
    [
        ("external_encryption_key", {"bootstrap_admin_password": "Valid-Pass-1234", "bootstrap_secret": "valid-random-setup-secret-16chars"}),
        ("bootstrap_admin_password", {"external_encryption_key": "x" * 40, "bootstrap_secret": "valid-random-setup-secret-16chars"}),
        ("bootstrap_secret", {"external_encryption_key": "x" * 40, "bootstrap_admin_password": "Valid-Pass-1234"}),
    ],
)
def test_env_example_placeholders_are_rejected_in_production(field, valid_others):
    example_key = {"external_encryption_key": "APP_EXTERNAL_ENCRYPTION_KEY", "bootstrap_admin_password": "APP_BOOTSTRAP_ADMIN_PASSWORD", "bootstrap_secret": "APP_BOOTSTRAP_SECRET"}[field]
    placeholder = _example_env_values()[example_key]
    with pytest.raises(ValidationError) as err:
        Settings(environment="production", **{**valid_others, field: placeholder})
    assert example_key in str(err.value)
    # The offending example value must not be echoed back in the error.
    assert placeholder not in str(err.value)



# --- FEAT-04: worker restart recovery + retry lineage ---
def _queued_job(client, upstream):
    _confirm_all(client, work := _work(client))
    model = client.post(
        "/api/v1/admin/model-configs",
        json={"display_name": "模型A", "model_name": "hold-gen", "base_url": upstream.base_url, "api_key": "k"},
    ).json()
    job = client.post(
        f"/api/v1/works/{work}/generation-jobs",
        json={"model_config_version_id": model["id"], "target_type": "EPISODE", "target_path": {"episode": 1}},
    )
    assert job.status_code == 201, job.text
    return work, model, job.json()["id"]


def test_recovery_interrupts_running_and_keeps_or_validates_queued(tmp_path: Path):
    with model_server() as upstream, _client(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        _login(client, "admin", "Admin-password-123")
        factory = client.app.state.session_factory

        running_work, _, running_job = _queued_job(client, upstream)
        with factory() as db:
            db.get(GenerationJob, running_job).status = "RUNNING"
            db.commit()

        healthy_work, _, healthy_job = _queued_job(client, upstream)

        stale_work, _, stale_job = _queued_job(client, upstream)
        # Mark the work inactive directly (bypassing the trash endpoint, which
        # would cancel the queued job itself) so recovery must reject it.
        with factory() as db:
            db.get(Work, stale_work).status = "TRASHED"
            db.commit()

        enqueued = set(lifecycle.recover_generation_state(factory))

        with factory() as db:
            r = db.get(GenerationJob, running_job)
            assert r.status == "FAILED" and r.error_code == "WORKER_INTERRUPTED"
            # The running job's lock must be released.
            assert db.scalar(select(WorkAIActivity).where(WorkAIActivity.work_id == running_work)) is None

            s = db.get(GenerationJob, stale_job)
            assert s.status == "FAILED" and s.error_code == "WORK_INACTIVE"

        assert healthy_job in enqueued
        assert running_job not in enqueued and stale_job not in enqueued


def test_retry_creates_new_job_with_lineage(tmp_path: Path):
    with model_server() as upstream, _client(tmp_path) as client:
        assert client.post("/api/v1/admin/bootstrap", json={"username": "admin", "password": "Admin-password-123"}).status_code == 201
        _login(client, "admin", "Admin-password-123")
        factory = client.app.state.session_factory
        work, _model, job_id = _queued_job(client, upstream)

        with factory() as db:
            original = db.get(GenerationJob, job_id)
            original.status = "FAILED"
            original.error_code = "UPSTREAM_ERROR"
            original.retry_count = 2
            db.query(WorkAIActivity).filter(WorkAIActivity.work_id == work).delete()
            db.commit()

        retried = client.post(f"/api/v1/works/{work}/generation-jobs/{job_id}/retry")
        assert retried.status_code == 201, retried.text
        created = retried.json()
        assert created["id"] != job_id
        assert created["retry_of_job_id"] == job_id
        assert created["retry_count"] == 3
        assert created["status"] == "QUEUED"

        with factory() as db:
            # Original record stays immutable as a FAILED ancestor.
            assert db.get(GenerationJob, job_id).status == "FAILED"

        # Only one active job per work: a second retry is rejected.
        blocked = client.post(f"/api/v1/works/{work}/generation-jobs/{job_id}/retry")
        assert blocked.status_code == 409
