import json
import time
from copy import deepcopy
from threading import Event
import pytest
from sqlalchemy import select
from app.models import ModelProvider, ModelConfigVersion, ModelInvocationRecord
from app.crypto import decrypt_secret
from app.model_adapters import AdapterResponse, MockModelAdapter
from test_versions_generation_export import _client, _login, _confirm_all

@pytest.fixture
def studio(tmp_path):
    with _client(tmp_path) as client:
        client.post('/api/v1/admin/bootstrap', json={'username': 'admin', 'password': 'Admin-password-123'})
        _login(client, 'admin', 'Admin-password-123')
        work = client.post('/api/v1/works', json={'title': 'private-title'}).json()['id']
        _confirm_all(client, work)
        config = client.post('/api/v1/admin/model-configs', json={'display_name': '测试模型', 'adapter': 'mock', 'model_name': 'test', 'api_key': 'private-credential'}).json()
        draft = {'episodes': [{'number': n, 'title': f'集{n}', 'scenes': [{'number': 1, 'heading': '地点', 'fragments': [{'id': f'f{n}', 'blocks': [{'type': 'ACTION', 'text': f'private-script-{n}'}]}, {'id': f'other{n}', 'blocks': [{'type': 'DIALOGUE', 'text': '保留'}]}]}]} for n in [1, 2]]}
        assert client.patch(f'/api/v1/works/{work}/draft', json={'content': draft}).status_code == 200
        yield (client, work, config['id'], draft)

def job(client, work, config, auto=False):
    response = client.post(f'/api/v1/works/{work}/generation-jobs', json={'model_config_version_id': config, 'target_type': 'FRAGMENT', 'target_path': {'episode': 1, 'scene': 1, 'fragment': 'f1'}, 'instruction': '新动作', 'auto_run': auto})
    assert response.status_code == 201, response.text
    return response.json()['id']

def wait_job(client, work, job_id):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        response = client.get(f'/api/v1/works/{work}/generation-jobs/{job_id}').json()
        if response['status'] not in {'QUEUED', 'RUNNING'}:
            return response
        time.sleep(0.03)
    raise AssertionError('job never finished')

def test_replacement_and_encrypted_credentials(studio):
    client, work, config, draft = studio
    auth = config
    ident = job(client, work, config, True)
    assert wait_job(client, work, ident)['status'] == 'SUCCEEDED'
    saved = client.get(f'/api/v1/works/{work}/draft').json()['content']
    expected = deepcopy(draft)
    expected['episodes'][0]['scenes'][0]['fragments'][0]['blocks'] = [{'type': 'ACTION', 'text': 'mock:新动作'}]
    assert saved == expected
    assert len(client.get(f'/api/v1/works/{work}/versions').json()) == 1
    assert 'private-credential' not in client.get('/api/v1/admin/model-configs').text
    with client.app.state.session_factory() as db:
        credential = db.scalar(select(ModelConfigVersion)).credential_ciphertext
        assert 'private-credential' not in credential
        assert decrypt_secret(credential, client.app.state.settings.external_encryption_key) == 'private-credential'

@pytest.mark.parametrize('text,stop', [('not JSON', 'stop'), ('{"id":"f1","blocks":[]}', 'stop'), ('{"id":"wrong","blocks":[{"type":"ACTION","text":"x"}]}', 'stop'), ('{"id":"f1","blocks":[{"type":"ACTION","text":"x"}]}', 'length')])
def test_invalid_generation_preserves_draft(studio, monkeypatch, text, stop):
    client, work, config, draft = studio
    monkeypatch.setattr(MockModelAdapter, 'invoke', lambda self, **kwargs: AdapterResponse(text, stop))
    ident = job(client, work, config)
    done = client.post(f'/api/v1/works/{work}/generation-jobs/{ident}/run').json()
    assert done['status'] == 'FAILED' and done['error_code'] == 'SCRIPT_STRUCTURE_INVALID'
    assert client.get(f'/api/v1/works/{work}/draft').json()['content'] == draft
    assert client.get(f'/api/v1/works/{work}/versions').json() == []

def test_direct_conversation_metadata_contains_no_script_or_prompt(studio):
    client, work, config, draft = studio
    response = client.post(f'/api/v1/works/{work}/conversations', json={'message': '用户提问', 'model_config_version_id': config})
    assert response.status_code == 201, response.text
    assert response.json()['context_summary']['script_scope'] == 'NONE'
    assert client.post(f'/api/v1/works/{work}/conversations', json={'message': '第二轮', 'model_config_version_id': config}).status_code == 201
    assert client.get(f'/api/v1/works/{work}/versions').json() == []
    invocation = client.get(f'/api/v1/works/{work}/model-invocations').text
    assert '用户提问' not in invocation and 'private-credential' not in invocation

@pytest.mark.parametrize('cancel', [True, False])
def test_cancel_and_concurrent_edit_preserve_draft(studio, monkeypatch, cancel):
    client, work, config, draft = studio
    started, release, finished = (Event(), Event(), Event())
    original = MockModelAdapter.invoke

    def invoke(self, **kwargs):
        started.set()
        assert release.wait(5)
        try:
            return original(self, **kwargs)
        finally:
            finished.set()
    monkeypatch.setattr(MockModelAdapter, 'invoke', invoke)
    ident = job(client, work, config, True)
    assert started.wait(3)
    if cancel:
        assert client.post(f'/api/v1/works/{work}/generation-jobs/{ident}/cancel').status_code == 200
    else:
        draft['episodes'][1]['title'] = '协作修改'
        assert client.patch(f'/api/v1/works/{work}/draft', json={'content': draft}).status_code == 409
    release.set()
    assert finished.wait(3)
    response = wait_job(client, work, ident)
    assert response['status'] == ('CANCELED' if cancel else 'SUCCEEDED')
    assert len(client.get(f'/api/v1/works/{work}/versions').json()) == (0 if cancel else 1)
    if cancel:
        assert client.get(f'/api/v1/works/{work}/draft').json()['content'] == draft

def test_backup_restores_draft_and_rejects_stale_clients(studio):
    client, work, config, draft = studio
    backup = client.post('/api/v1/admin/backups').json()
    metadata = client.get(f'/api/v1/works/{work}').json()
    changed = deepcopy(draft)
    changed['episodes'][0]['title'] = '备份后的修改'
    assert client.patch(f'/api/v1/works/{work}/draft', json={'content': changed}).status_code == 200
    before = client.get(f'/api/v1/works/{work}').json()['revision']
    assert client.post(f"/api/v1/admin/backups/{backup['id']}/restore").status_code == 200
    assert client.get(f'/api/v1/works/{work}/draft').json()['content'] == draft
    assert client.get(f'/api/v1/works/{work}').json()['revision'] > before
    assert client.patch(f'/api/v1/works/{work}/draft', json={'content': changed, 'expected_revision': before}).status_code == 409

def test_backup_can_restore_template_bindings_to_fresh_database(studio, tmp_path):
    from pathlib import Path
    from app.models import BackupRecord
    from app.main import create_app
    from app.config import Settings
    from test_versions_generation_export import CsrfClient
    client, work, config, draft = studio
    backup = client.post('/api/v1/admin/backups').json()
    raw = Path(backup['path']).read_bytes()
    file = tmp_path / 'copied-backup.json'
    file.write_bytes(raw)
    with CsrfClient(create_app(Settings(database_url=f"sqlite:///{tmp_path / 'fresh.db'}", cookie_secure=False, backup_dir=str(tmp_path)))) as fresh:
        fresh.post('/api/v1/admin/bootstrap', json={'username': 'recovery', 'password': 'Recovery-password-123'})
        _login(fresh, 'recovery', 'Recovery-password-123')
        with fresh.app.state.session_factory() as db:
            record = BackupRecord(path=str(file), checksum=backup['checksum'])
            db.add(record)
            db.commit()
            backup_id = record.id
        restored = fresh.post(f'/api/v1/admin/backups/{backup_id}/restore')
        assert restored.status_code == 200, restored.text
        fresh.post('/api/v1/auth/logout')
        _login(fresh, 'admin', 'Admin-password-123-changed')
        assert fresh.get(f'/api/v1/works/{work}').status_code == 200
        assert fresh.get(f'/api/v1/works/{work}/draft').json()['content'] == draft
        assert fresh.get(f'/api/v1/works/{work}').json()['template_definition']

def test_transient_errors_retry_and_produce_only_one_version(studio, monkeypatch):
    from app.model_adapters import ModelAdapterError
    client, work, config, draft = studio
    calls = []
    original = MockModelAdapter.invoke

    def invoke(self, **kwargs):
        calls.append(kwargs)
        if len(calls) < 3:
            raise ModelAdapterError('RATE_LIMITED', 'provider unavailable', retryable=True)
        return original(self, **kwargs)
    monkeypatch.setattr(MockModelAdapter, 'invoke', invoke)
    ident = job(client, work, config, True)
    done = wait_job(client, work, ident)
    assert done['status'] == 'SUCCEEDED' and done['retry_count'] == 2
    assert len(calls) == 3 and len(client.get(f'/api/v1/works/{work}/versions').json()) == 1

def test_fatal_model_error_has_safe_metadata(studio, monkeypatch):
    from app.model_adapters import ModelAdapterError
    client, work, config, draft = studio

    def invoke(self, **kwargs):
        raise ModelAdapterError('AUTHENTICATION_ERROR', 'private-credential')
    monkeypatch.setattr(MockModelAdapter, 'invoke', invoke)
    auth = config
    failed = client.post(f'/api/v1/works/{work}/conversations', json={'message': 'private-prompt', 'model_config_version_id': config})
    assert failed.status_code == 502 and 'private-credential' not in failed.text
    records = client.get(f'/api/v1/works/{work}/model-invocations')
    assert 'private-prompt' not in records.text and 'private-credential' not in records.text
    assert records.json()[-1]['error_code'] == 'AUTHENTICATION_ERROR'

def test_changed_input_is_not_transmitted_again_on_retry(studio, monkeypatch):
    from app.model_adapters import ModelAdapterError
    client, work, config, draft = studio
    calls = []

    def invoke(self, **kwargs):
        calls.append(kwargs)
        edited = deepcopy(draft)
        edited['episodes'][0]['title'] = '模型请求期间的新修改'
        assert client.patch(f'/api/v1/works/{work}/draft', json={'content': edited}).status_code == 409
        raise ModelAdapterError('RATE_LIMITED', 'temporarily unavailable', retryable=True)
    monkeypatch.setattr(MockModelAdapter, 'invoke', invoke)
    ident = job(client, work, config, True)
    done = wait_job(client, work, ident)
    assert done['status'] == 'FAILED' and done['error_code'] == 'RATE_LIMITED'
    assert len(calls) == 3
    assert client.get(f'/api/v1/works/{work}/versions').json() == []
