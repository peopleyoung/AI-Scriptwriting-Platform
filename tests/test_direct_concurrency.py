"""Atomic chat/generation occupancy at the public API on both database engines."""
import os
import subprocess
import sys
import json
import socket
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Thread
import time

import pytest
import sqlalchemy as sa
import httpx
import uvicorn

from app.config import Settings
from app.main import create_app
from http_model_server import model_server
from test_model_migration import migration_url
from test_versions_generation_export import CsrfClient, _login, _confirm_all


@pytest.fixture
def concurrent_studio(migration_url, tmp_path):
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], env={**os.environ, 'APP_DATABASE_URL': migration_url}, check=True, capture_output=True)
    app = create_app(Settings(database_url=migration_url, auto_create_schema=False, cookie_secure=False, backup_dir=str(tmp_path / 'backups')))
    with model_server() as upstream, CsrfClient(app) as client:
        client.post('/api/v1/admin/bootstrap', json={'username': 'admin', 'password': 'Admin-password-123'})
        _login(client, 'admin', 'Admin-password-123')
        config = client.post('/api/v1/admin/model-configs', json={'model_name': 'hold-concurrent', 'base_url': upstream.base_url, 'api_key': 'test-key'}).json()['id']
        work = client.post('/api/v1/works', json={'title': '并发验收'}).json()['id']
        _confirm_all(client, work)
        yield client, upstream, config, work


@pytest.mark.parametrize('kinds', [('chat', 'chat'), ('chat', 'generation'), ('generation', 'chat'), ('generation', 'generation')])
def test_only_one_ai_call_starts_for_a_work(concurrent_studio, kinds):
    client, upstream, config, work = concurrent_studio
    barrier = Barrier(2)
    def send(index):
        barrier.wait(timeout=3)
        common = {'model_config_version_id': config, 'idempotency_key': str(index)}
        if kinds[index] == 'chat':
            return client.post(f'/api/v1/works/{work}/conversations', json={**common, 'message': '讨论'})
        return client.post(f'/api/v1/works/{work}/generation-jobs', json={**common, 'target_type': 'EPISODE', 'target_path': {'episode': 1}, 'auto_run': True})
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(send, n) for n in range(2)]
        try:
            assert upstream.started.wait(5)
            activity = client.get(f'/api/v1/works/{work}/ai-activity')
            assert activity.status_code == 200 and activity.json() is not None
            assert len(upstream.requests) == 1
            deadline = time.monotonic() + 5
            while not any(call.done() and call.result().status_code == 409 for call in calls):
                assert time.monotonic() < deadline, 'competing call was not rejected while upstream was held'
                Event().wait(.03)
        finally:
            upstream.release.set()
        responses = [call.result(timeout=8) for call in calls]
    assert sorted(r.status_code for r in responses) == [201, 409], [r.text for r in responses]
    assert len(upstream.requests) == 1

@pytest.mark.parametrize('mutation', [{'display_name': '新版'}, {'enabled': False}])
def test_model_update_and_new_call_do_not_deadlock(concurrent_studio, mutation):
    client, upstream, config, work = concurrent_studio
    if client.app.state.engine.dialect.name != 'postgresql':
        pytest.skip('row-lock scheduling requires PostgreSQL')
    engine = client.app.state.engine
    upstream.release.set()
    # A third real database transaction delays the update at a known row.
    # API requests still execute every production permission and locking step.
    with engine.connect() as holder:
        transaction = holder.begin()
        pid = holder.execute(sa.text('select pg_backend_pid()')).scalar_one()
        holder.execute(sa.text('select id from model_config_versions where id=:id for update'), {'id': config})
        with ThreadPoolExecutor(max_workers=2) as pool:
            updating = pool.submit(client.patch, f'/api/v1/admin/model-configs/{config}', json=mutation)
            try:
                deadline = time.monotonic() + 4
                while True:
                    with engine.connect() as observer:
                        waiting = observer.execute(sa.text('select count(*) from pg_stat_activity where :pid=ANY(pg_blocking_pids(pid))'), {'pid': pid}).scalar_one()
                    if waiting:
                        break
                    assert time.monotonic() < deadline, 'update did not reach the held configuration'
                    Event().wait(.03)
                invoking = pool.submit(client.post, f'/api/v1/works/{work}/conversations', json={'message': '同时发送', 'model_config_version_id': config})
                deadline = time.monotonic() + 4
                while True:
                    with engine.connect() as observer:
                        waiting = observer.execute(sa.text("select count(*) from pg_stat_activity where datname=current_database() and wait_event_type='Lock'")).scalar_one()
                    if waiting >= 2:
                        break
                    assert time.monotonic() < deadline, 'call did not reach the contending lock'
                    Event().wait(.03)
            finally:
                transaction.rollback()
            changed = updating.result(timeout=6)
            response = invoking.result(timeout=6)
    assert changed.status_code == 200, changed.text
    assert response.status_code == 409, response.text
    assert upstream.requests == []


def test_replayed_live_stream_does_not_hold_work_lock_or_repeat_model_call(concurrent_studio):
    client, upstream, config, work = concurrent_studio
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    server = uvicorn.Server(uvicorn.Config(client.app, lifespan='off', log_level='error', timeout_graceful_shutdown=1))
    thread = Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    body = {'message': '同一次发送', 'model_config_version_id': config, 'idempotency_key': 'replayed-stream'}
    headers = {'X-CSRF-Token': client.cookies.get('scriptwriting_csrf')}
    url = f'http://127.0.0.1:{listener.getsockname()[1]}'
    def messages(response):
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith('data: '):
                yield json.loads(line[6:])
    try:
        with httpx.Client(base_url=url, cookies=dict(client.cookies), timeout=4) as browser:
            with browser.stream('POST', f'/api/v1/works/{work}/conversations/stream', json=body, headers=headers) as first:
                first_messages = messages(first)
                initial = next(first_messages)
                assert upstream.started.wait(3)
                with browser.stream('POST', f'/api/v1/works/{work}/conversations/stream', json=body, headers=headers) as replay:
                    replay_messages = messages(replay)
                    assert next(replay_messages)['id'] == initial['id']
                    saved = browser.patch(f'/api/v1/works/{work}/draft', json={'content': {}}, headers=headers)
                    assert saved.status_code == 200, saved.text
                    upstream.release.set()
                    assert list(first_messages)[-1]['status'] == 'SUCCEEDED'
                    assert list(replay_messages)[-1]['status'] == 'SUCCEEDED'
        assert len(upstream.requests) == 1
        assert len(client.get(f'/api/v1/works/{work}/conversations').json()) == 1
    finally:
        upstream.release.set()
        server.should_exit = True
        thread.join(timeout=3)
        listener.close()
