import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings
from app.main import create_app
from app.crypto import decrypt_bytes, encrypt_bytes
from app.models import BackupRecord
from test_call_lifecycle import direct_studio
from test_model_migration import migration_url
from test_versions_generation_export import CsrfClient, _login, _confirm_all


@pytest.mark.parametrize('backup_format', [1, 2])
def test_fresh_restore_keeps_models_snapshots_partial_history_jobs_and_no_keys(direct_studio, tmp_path, migration_url, backup_format):
    source, upstream, model, work = direct_studio
    upstream.release.set()
    _confirm_all(source, work)
    complete_request = {'message': '完整讨论', 'model_config_version_id': model['id'], 'idempotency_key': 'backed-up-once'}
    complete = source.post(f'/api/v1/works/{work}/conversations', json=complete_request)
    assert complete.status_code == 201, complete.text
    queued = source.post(f'/api/v1/works/{work}/generation-jobs', json={'model_config_version_id': model['id'], 'target_type': 'EPISODE', 'target_path': {'episode': 1}}).json()
    done = source.post(f"/api/v1/works/{work}/generation-jobs/{queued['id']}/run").json()
    assert done['status'] == 'SUCCEEDED'
    dropped = source.patch(f"/api/v1/admin/model-configs/{model['id']}", json={'model_name': 'drop-model'}).json()
    partial = source.post(f'/api/v1/works/{work}/conversations', json={'message': '未完成讨论', 'model_config_version_id': dropped['id']})
    assert partial.status_code == 502
    conversations = source.get(f'/api/v1/works/{work}/conversations').json()
    backup = source.post('/api/v1/admin/backups').json()
    raw = Path(backup['path']).read_bytes()
    plain = decrypt_bytes(raw, source.app.state.settings.external_encryption_key).decode()
    assert 'old-key' not in plain and 'credential_ciphertext' not in plain
    snapshot = json.loads(plain)
    assert len(snapshot['generation_jobs']) == 1 and len(snapshot['invocations']) == 3
    if backup_format == 1:
        # These are the fields the actual format-1 writer included. It did not
        # store call/job attribution, so restore must tolerate their absence.
        snapshot['format'] = 1
        for key in ['model_configurations', 'generation_jobs', 'invocations', 'consents']:
            snapshot.pop(key)
        legacy_config_fields = {'id', 'provider_id', 'version', 'model_name', 'endpoint', 'parameters', 'status'}
        legacy_chat_fields = {'id', 'work_id', 'user_id', 'user_message', 'assistant_message', 'status', 'created_at'}
        snapshot['model_configs'] = [{k: v for k, v in row.items() if k in legacy_config_fields} for row in snapshot['model_configs']]
        snapshot['conversations'] = [{k: v for k, v in row.items() if k in legacy_chat_fields} for row in snapshot['conversations']]
        raw = encrypt_bytes(json.dumps(snapshot, ensure_ascii=False).encode(), source.app.state.settings.external_encryption_key)
    fresh_path = tmp_path / 'fresh'
    fresh_path.mkdir()
    artifact = fresh_path / 'copy.json'
    artifact.write_bytes(raw)
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], env={**os.environ, 'APP_DATABASE_URL': migration_url}, check=True, capture_output=True)
    app = create_app(Settings(database_url=migration_url, auto_create_schema=False, cookie_secure=False, backup_dir=str(fresh_path / 'backups')))
    with CsrfClient(app) as restored:
        restored.post('/api/v1/admin/bootstrap', json={'username': 'recovery', 'password': 'Recovery-password-123'})
        _login(restored, 'recovery', 'Recovery-password-123')
        # The existing restore entry accepts registered local backup artifacts.
        with restored.app.state.session_factory() as db:
            record = BackupRecord(path=str(artifact), checksum=hashlib.sha256(raw).hexdigest())
            db.add(record); db.commit(); backup_id = record.id
        result = restored.post(f'/api/v1/admin/backups/{backup_id}/restore')
        assert result.status_code == 200, result.text
        restored.post('/api/v1/auth/logout')
        _login(restored, 'admin', 'Admin-password-123-changed')
        rows = restored.get(f'/api/v1/works/{work}/conversations').json()
        assert [(x['id'], x['status'], x['assistant_message']) for x in rows] == [(x['id'], x['status'], x['assistant_message']) for x in conversations]
        if backup_format == 2:
            assert [(x['model_config_version_id'], x['model_display_name'], x['context_summary']) for x in rows] == [(x['model_config_version_id'], x['model_display_name'], x['context_summary']) for x in conversations]
            replay = restored.post(f'/api/v1/works/{work}/conversations', json=complete_request)
            assert replay.status_code == 201 and replay.json()['id'] == complete.json()['id']
        else:
            assert all(row['model_config_version_id'] is None for row in rows)
        jobs = restored.get(f'/api/v1/works/{work}/generation-jobs').json()
        if backup_format == 2:
            assert jobs[0]['id'] == done['id'] and jobs[0]['result_version_id'] == done['result_version_id']
        else:
            assert jobs == []
        assert restored.get(f'/api/v1/works/{work}/versions').json()[0]['id'] == done['result_version_id']
        assert restored.get(f'/api/v1/works/{work}/ai-activity').json() is None
        assert restored.get('/api/v1/model-configs').json() == []
        configs = restored.get('/api/v1/admin/model-configs').json()
        assert next(x for x in configs if x['id'] == model['id'])['status'] == 'RETIRED'
        updated = restored.patch(f"/api/v1/admin/model-configs/{dropped['id']}", json={'api_key': 'new-environment-key', 'model_name': 'normal-restored'}).json()
        restored.patch(f"/api/v1/admin/model-configs/{updated['id']}", json={'enabled': True})
        response = restored.post(f'/api/v1/works/{work}/conversations', json={'message': '恢复后继续', 'model_config_version_id': updated['id']})
        assert response.status_code == 201, response.text
        assert upstream.requests[-1]['authorization'] == 'Bearer new-environment-key'
