"""Opt-in integration against a dedicated disposable PostgreSQL database."""
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from app.config import Settings
from app.main import create_app
from app.model_adapters import MockModelAdapter
from test_versions_generation_export import CsrfClient, _confirm_all, _login

PG_TEST_ADMIN_PASSWORD = "Recovery-password-123"


@pytest.fixture
def isolated_postgres_url():
    original = os.environ.get('APP_TEST_POSTGRES_URL')
    if not original:
        pytest.skip('dedicated PostgreSQL database not configured')
    engine = sa.create_engine(original)
    schema = 'test_r4_' + uuid4().hex
    try:
        with engine.begin() as db:
            db.execute(sa.text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(original).update_query_dict({'options': f'-csearch_path={schema}'})
        yield url.render_as_string(hide_password=False)
    finally:
        with engine.begin() as db:
            db.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        engine.dispose()


@pytest.mark.skipif(not os.environ.get('APP_TEST_POSTGRES_URL'), reason='dedicated PostgreSQL database not configured')
def test_postgresql_migrations_concurrent_jobs_and_cancellation(monkeypatch, isolated_postgres_url):
    url = isolated_postgres_url
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=root, env={**os.environ, 'APP_DATABASE_URL':url}, check=True, capture_output=True)
    app = create_app(Settings(database_url=url, auto_create_schema=False, cookie_secure=False, bootstrap_admin_username=None, bootstrap_admin_password=None))
    with CsrfClient(app) as client:
        # This URL must point at an isolated CI/test database, never production.
        username='pg-'+uuid4().hex[:8]
        boot=client.post('/api/v1/admin/bootstrap',json={'username':username,'password':PG_TEST_ADMIN_PASSWORD})
        assert boot.status_code == 201, boot.text
        _login(client, username, PG_TEST_ADMIN_PASSWORD)
        work=client.post('/api/v1/works',json={'title':'PostgreSQL smoke'}).json()['id']
        _confirm_all(client,work)
        config=client.post('/api/v1/admin/model-configs',json={'adapter':'mock','model_name':'mock'}).json()['id']
        def create(key):
            return client.post(f'/api/v1/works/{work}/generation-jobs',json={'model_config_version_id':config,'target_type':'EPISODE','target_path':{'episode':1},'idempotency_key':key})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(create, ['a','b']))
        assert sorted(response.status_code for response in responses)==[201,409]
        queued=next(response.json() for response in responses if response.status_code==201)
        started,release=Event(),Event()
        original=MockModelAdapter.invoke
        def invoke(self, **kwargs):
            started.set()
            assert release.wait(5)
            return original(self, **kwargs)
        monkeypatch.setattr(MockModelAdapter,'invoke',invoke)
        with ThreadPoolExecutor(max_workers=1) as pool:
            run=pool.submit(client.post,f"/api/v1/works/{work}/generation-jobs/{queued['id']}/run")
            assert started.wait(3)
            canceled=client.post(f"/api/v1/works/{work}/generation-jobs/{queued['id']}/cancel")
            release.set()
            assert canceled.status_code==200, canceled.text
            assert run.result(timeout=5).json()['status']=='CANCELED'
        assert client.get(f'/api/v1/works/{work}/versions').json()==[]


@pytest.mark.skipif(not os.environ.get('APP_TEST_POSTGRES_URL'), reason='dedicated PostgreSQL database not configured')
def test_postgresql_upstream_approval_serializes_competing_downstream_approval(isolated_postgres_url):
    url = isolated_postgres_url
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=root, env={**os.environ, 'APP_DATABASE_URL': url}, check=True, capture_output=True)
    app = create_app(Settings(database_url=url, auto_create_schema=False, cookie_secure=False, bootstrap_admin_username=None, bootstrap_admin_password=None))
    with CsrfClient(app) as client:
        username = 'r4-pg-' + uuid4().hex[:8]
        bootstrap = client.post('/api/v1/admin/bootstrap', json={'username': username, 'password': PG_TEST_ADMIN_PASSWORD})
        assert bootstrap.status_code == 201, bootstrap.text
        _login(client, username, PG_TEST_ADMIN_PASSWORD)
        work = client.post('/api/v1/works', json={'title': 'R4 concurrent approval'}).json()['id']
        _confirm_all(client, work)
        proposals = {}
        for stage, content in (
            ('brief', {'theme': '新方向'}),
            ('outline', {'premise': '新大纲'}),
        ):
            response = client.post(f'/api/v1/works/{work}/change-proposals', json={'stage': stage, 'content': content})
            assert response.status_code == 201, response.text
            proposals[stage] = response.json()['id']

        def approve(stage):
            return client.post(f'/api/v1/works/{work}/change-proposals/{proposals[stage]}/approve')

        with ThreadPoolExecutor(max_workers=2) as pool:
            upstream = pool.submit(approve, 'brief')
            downstream = pool.submit(approve, 'outline')
            responses = {'brief': upstream.result(timeout=10), 'outline': downstream.result(timeout=10)}
        assert responses['brief'].status_code == 200, responses['brief'].text
        assert responses['outline'].status_code in {200, 409}, responses['outline'].text
        creative = client.get(f'/api/v1/works/{work}/creative').json()
        assert creative['states']['brief'] == 'CONFIRMED'
        assert creative['confirmed']['brief'] == {'theme': '新方向'}
        assert creative['states']['outline'] == 'NEEDS_RECONFIRMATION'
        assert creative['confirmed']['outline'] is None
        proposal_rows = {row['id']: row for row in client.get(f'/api/v1/works/{work}/change-proposals').json()}
        outline = proposal_rows[proposals['outline']]
        assert outline['status'] in {'APPROVED', 'STALE'}
        if outline['status'] == 'STALE':
            assert outline['stale_reason'] == 'UPSTREAM_CONFIRMATION_CHANGED'
