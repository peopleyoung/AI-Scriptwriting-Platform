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

from app.config import Settings
from app.main import create_app
from app.model_adapters import MockModelAdapter
from test_versions_generation_export import CsrfClient, _confirm_all, _login


@pytest.mark.skipif(not os.environ.get('APP_TEST_POSTGRES_URL'), reason='dedicated PostgreSQL database not configured')
def test_postgresql_migrations_concurrent_jobs_and_cancellation(monkeypatch):
    url = os.environ['APP_TEST_POSTGRES_URL']
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=root, env={**os.environ, 'APP_DATABASE_URL':url}, check=True, capture_output=True)
    app = create_app(Settings(database_url=url, auto_create_schema=False, cookie_secure=False, bootstrap_admin_username=None, bootstrap_admin_password=None))
    with CsrfClient(app) as client:
        # This URL must point at an isolated CI/test database, never production.
        username='pg-'+uuid4().hex[:8]
        boot=client.post('/api/v1/admin/bootstrap',json={'username':username,'password':'Postgres-password-123'})
        assert boot.status_code == 201, boot.text
        _login(client, username, 'Postgres-password-123')
        work=client.post('/api/v1/works',json={'title':'PostgreSQL smoke'}).json()['id']
        _confirm_all(client,work)
        provider=client.post('/api/v1/admin/providers',json={'slug':username,'name':'mock','adapter':'mock','retention_description':'local','deletion_description':'test','incident_description':'test'}).json()['id']
        client.patch(f'/api/v1/admin/providers/{provider}',json={'enabled':True})
        config=client.post('/api/v1/admin/model-configs',json={'provider_id':provider,'model_name':'mock'}).json()['id']
        authorization=client.post(f'/api/v1/works/{work}/consents',json={'model_config_version_id':config,'data_categories':['script']}).json()['id']
        def create(key):
            return client.post(f'/api/v1/works/{work}/generation-jobs',json={'consent_id':authorization,'target_type':'EPISODE','target_path':{'episode':1},'idempotency_key':key})
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
