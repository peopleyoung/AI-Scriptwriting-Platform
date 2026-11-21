"""Run real legacy upgrades on disposable SQLite/PostgreSQL databases."""
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from app.config import Settings
from app.crypto import decrypt_secret, encrypt_secret
from app.main import create_app
from app.security import hash_password
from test_versions_generation_export import CsrfClient, _login


@pytest.fixture(params=['sqlite', 'postgresql'])
def migration_url(request, tmp_path):
    if request.param == 'sqlite':
        yield f'sqlite:///{tmp_path / "migration.db"}'
        return
    original = os.environ.get('APP_TEST_POSTGRES_URL')
    if not original:
        pytest.skip('dedicated PostgreSQL test database not configured')
    engine = sa.create_engine(original)
    schema = 'model_migration_' + uuid4().hex
    with engine.begin() as db:
        db.execute(sa.text(f'CREATE SCHEMA {schema}'))
    url = sa.engine.make_url(original).update_query_dict({'options': f'-csearch_path={schema}'})
    try:
        yield url.render_as_string(hide_password=False)
    finally:
        with engine.begin() as db:
            db.execute(sa.text(f'DROP SCHEMA {schema} CASCADE'))
        engine.dispose()


def test_legacy_models_preserve_versions_credentials_and_references(migration_url, tmp_path):
    env = {**os.environ, 'APP_DATABASE_URL': migration_url}
    def upgrade(revision):
        subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', revision], env=env, check=True, capture_output=True)
    upgrade('0005_active_generation_job')
    engine = sa.create_engine(migration_url)
    meta = sa.MetaData()
    meta.reflect(engine)
    secret = encrypt_secret('migration-test-key', Settings().external_encryption_key)
    with engine.begin() as db:
        db.execute(meta.tables['users'].insert().values(id='admin', username='migration-admin', password_hash=hash_password('Migration-password-123'), role='ADMIN', is_active=True, must_change_password=False))
        db.execute(meta.tables['model_providers'].insert().values(id='provider', slug='legacy', name='Legacy', adapter='openai', enabled=True, credential_ciphertext=secret))
        for number, name, state in [(1, 'a', 'RETIRED'), (2, 'b', 'RETIRED'), (3, 'b-new', 'ACTIVE')]:
            db.execute(meta.tables['model_config_versions'].insert().values(id=f'config-{number}', provider_id='provider', version=number, model_name=name, endpoint='https://models.example/v1', parameters_json={}, credential_ciphertext=secret, status=state))
        db.execute(meta.tables['audit_logs'].insert().values(id='replacement', event_type='MODEL_CONFIG_VERSION_CREATED', metadata_json={'config_id': 'config-3', 'replaced_config_id': 'config-2'}))
        db.execute(meta.tables['works'].insert().values(id='work', title='Legacy', owner_id='admin', settings_json={}, status='ACTIVE', revision=0, draft_json={}))
        db.execute(meta.tables['work_members'].insert().values(id='member', work_id='work', user_id='admin', role='OWNER', status='ACCEPTED'))
        db.execute(meta.tables['external_consents'].insert().values(id='consent', work_id='work', input_revision=0, model_config_version_id='config-1', data_categories_json=['script'], status='REVOKED', granted_by_user_id='admin'))
        db.execute(meta.tables['generation_jobs'].insert().values(id='job', work_id='work', requested_by_user_id='admin', idempotency_key='old', input_revision=0, target_type='WORK', target_path_json={}, model_config_version_id='config-1', consent_id='consent', status='SUCCEEDED', retry_count=0))
        db.execute(meta.tables['model_invocation_records'].insert().values(id='invocation', work_id='work', provider_id='provider', model_config_version_id='config-1', status='SUCCEEDED', usage_json={}, trace_id='old'))
        db.execute(meta.tables['creative_conversations'].insert().values(id='message', work_id='work', user_id='admin', user_message='旧问题', assistant_message='旧回复', status='SUCCEEDED', invocation_id='invocation'))
    upgrade('head')
    app = create_app(Settings(database_url=migration_url, auto_create_schema=False, cookie_secure=False, backup_dir=str(tmp_path / 'backups')))
    with CsrfClient(app) as client:
        _login(client, 'migration-admin', 'Migration-password-123')
        versions = {x['id']: x for x in client.get('/api/v1/admin/model-configs').json()}
        assert versions['config-1']['model_id'] != versions['config-2']['model_id']
        assert versions['config-2']['model_id'] == versions['config-3']['model_id']
        assert [x['id'] for x in client.get('/api/v1/model-configs').json()] == ['config-3']
        assert versions['config-1']['status'] == 'RETIRED'
        assert client.get('/api/v1/works/work/generation-jobs').json()[0]['id'] == 'job'
        assert client.get('/api/v1/works/work/model-invocations').json()[0]['model_config_version_id'] == 'config-1'
        assert client.get('/api/v1/works/work/conversations').json()[0]['invocation_id'] == 'invocation'
    # Credentials are deliberately inspected only at this migration seam.
    with engine.connect() as db:
        ciphertext = db.execute(sa.text("SELECT credential_ciphertext FROM model_config_versions WHERE id='config-3'")).scalar_one()
        assert ciphertext == secret
        assert decrypt_secret(ciphertext, Settings().external_encryption_key) == 'migration-test-key'
    engine.dispose()


def test_r4_migration_preserves_legacy_baselines_and_classifies_proposals(migration_url, tmp_path):
    env = {**os.environ, "APP_DATABASE_URL": migration_url}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "0009_generation_retry_lineage"], env=env, check=True, capture_output=True)
    engine = sa.create_engine(migration_url)
    metadata = sa.MetaData()
    metadata.reflect(engine)
    with engine.begin() as db:
        db.execute(metadata.tables["users"].insert().values(id="r4-owner", username="r4-owner", password_hash="unused", role="ADMIN", is_active=True, must_change_password=False))
        for work_id in ("legacy-complete", "legacy-broken"):
            db.execute(metadata.tables["works"].insert().values(id=work_id, title=work_id, owner_id="r4-owner", settings_json={}, status="ACTIVE", revision=7, draft_json={}))
            db.execute(metadata.tables["work_members"].insert().values(id=f"member-{work_id}", work_id=work_id, user_id="r4-owner", role="OWNER", status="ACCEPTED"))
        db.execute(metadata.tables["work_creative_data"].insert().values(
            id="creative-complete", work_id="legacy-complete",
            brief_json={"draft": "brief"}, characters_json=[{"name": "draft"}], outline_json={"draft": "outline"},
            brief_confirmed_json={"confirmed": "brief"}, characters_confirmed_json=[{"name": "confirmed"}], outline_confirmed_json={"confirmed": "outline"},
            brief_revision=2, characters_revision=3, outline_revision=4,
        ))
        db.execute(metadata.tables["work_creative_data"].insert().values(
            id="creative-broken", work_id="legacy-broken",
            brief_json=None, characters_json=None, outline_json=None,
            brief_confirmed_json=None, characters_confirmed_json=[{"name": "orphan"}], outline_confirmed_json={"premise": "orphan"},
            brief_revision=1, characters_revision=2, outline_revision=3,
        ))
        for proposal_id, work_id, stage, base_revision in (
            ("p-brief", "legacy-complete", "brief", 2),
            ("p-character", "legacy-complete", "characters", 3),
            ("p-outline", "legacy-complete", "outline", 4),
            ("p-brief-stale", "legacy-broken", "brief", 99),
        ):
            db.execute(metadata.tables["change_proposals"].insert().values(
                id=proposal_id, work_id=work_id, stage=stage, content_json={"legacy": proposal_id},
                base_revision=base_revision, status="PENDING", submitted_by_user_id="r4-owner",
            ))
    preflight = subprocess.run(
        [sys.executable, "scripts/r4_migration_preflight.py"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    classified = json.loads(preflight.stdout)
    assert classified["works_by_class"] == {"LEGACY_BROKEN_DEPENDENCY": 1, "LEGACY_COMPLETE_BASELINE": 1}
    assert classified["pending_proposals_by_stage_and_reason"] == {
        "brief:BASE_UNVERIFIED": 1,
        "brief:RETAIN_PENDING": 1,
        "characters:LEGACY_DEPENDENCY_UNVERIFIED": 1,
        "outline:LEGACY_DEPENDENCY_UNVERIFIED": 1,
    }
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True, capture_output=True)
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True, capture_output=True)
    current_preflight = subprocess.run(
        [sys.executable, "scripts/r4_migration_preflight.py"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    current_counts = json.loads(current_preflight.stdout)
    assert current_counts["works_by_class"] == {"R4_CURRENT_STATE": 2}
    assert current_counts["pending_proposals_by_stage_and_reason"] == {"brief:RETAIN_PENDING": 1}
    metadata.clear()
    metadata.reflect(engine)
    with engine.connect() as db:
        creative = metadata.tables["work_creative_data"]
        complete = db.execute(sa.select(creative).where(creative.c.work_id == "legacy-complete")).mappings().one()
        broken = db.execute(sa.select(creative).where(creative.c.work_id == "legacy-broken")).mappings().one()
        assert complete["stage_status_json"] == {"brief": "CONFIRMED", "characters": "CONFIRMED", "outline": "CONFIRMED"}
        assert complete["brief_revision"] == 2 and complete["characters_revision"] == 3 and complete["outline_revision"] == 4
        assert complete["confirmation_source_json"] == {"brief": "LEGACY_BASELINE", "characters": "LEGACY_BASELINE", "outline": "LEGACY_BASELINE"}
        assert complete["confirmation_history_complete"] is False
        assert broken["stage_status_json"] == {"brief": "DRAFT", "characters": "NEEDS_RECONFIRMATION", "outline": "NEEDS_RECONFIRMATION"}
        assert broken["characters_confirmed_json"] is None and broken["outline_confirmed_json"] is None
        assert broken["characters_json"] == [{"name": "orphan"}] and broken["outline_json"] == {"premise": "orphan"}
        assert broken["characters_revision"] == 3 and broken["outline_revision"] == 4
        assert db.execute(sa.text("SELECT revision FROM works WHERE id='legacy-broken'")).scalar_one() == 8
        proposal_rows = {row["id"]: row for row in db.execute(sa.select(metadata.tables["change_proposals"])).mappings()}
        assert proposal_rows["p-brief"]["status"] == "PENDING"
        assert proposal_rows["p-character"]["status"] == "STALE"
        assert proposal_rows["p-character"]["stale_reason"] == "LEGACY_DEPENDENCY_UNVERIFIED"
        assert proposal_rows["p-outline"]["status"] == "STALE"
        assert proposal_rows["p-outline"]["stale_reason"] == "LEGACY_DEPENDENCY_UNVERIFIED"
        assert proposal_rows["p-brief-stale"]["status"] == "STALE"
        assert proposal_rows["p-brief-stale"]["stale_reason"] == "BASE_UNVERIFIED"
        events = db.execute(sa.text("SELECT event_type, stage, source, reason, actor_user_id, confirmed_at FROM creative_confirmation_events WHERE work_id='legacy-complete'")).all()
        assert sum(row[0] == "LEGACY_BASELINE" for row in events) == 3
        assert sum(row[0] == "PROPOSAL_STALE" for row in events) == 2
        assert all(row[4] is None and row[5] is None for row in events)
        assert all(row[3] == "DEPENDENCY_UNVERIFIED" for row in events if row[0] == "LEGACY_BASELINE")
    engine.dispose()
