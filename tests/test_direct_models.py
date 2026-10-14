from test_versions_generation_export import _client, _login
from http_model_server import model_server
import pytest


def test_models_are_independent_and_credentials_never_returned(tmp_path):
    with _client(tmp_path) as client:
        client.post('/api/v1/admin/bootstrap', json={'username': 'admin', 'password': 'Admin-password-123'})
        _login(client, 'admin', 'Admin-password-123')
        body = {'display_name': '模型 A', 'model_name': 'a', 'base_url': 'https://models.example/v1', 'api_key': 'secret-first'}
        first = client.post('/api/v1/admin/model-configs', json=body)
        assert first.status_code == 201, first.text
        a = first.json()
        b = client.post('/api/v1/admin/model-configs', json={**body, 'model_name': 'b', 'display_name': '模型 B'}).json()
        assert a['model_id'] != b['model_id']
        assert a['adapter'] == 'openai'
        assert {x['id'] for x in client.get('/api/v1/model-configs').json()} == {a['id'], b['id']}
        updated = client.patch(f"/api/v1/admin/model-configs/{b['id']}", json={'display_name': '模型 B 新版', 'api_key': 'secret-second'})
        assert updated.status_code == 200, updated.text
        new = updated.json()
        assert new['model_id'] == b['model_id'] and new['version'] == 2
        assert client.get(f"/api/v1/admin/model-configs/{b['id']}").json()['display_name'] == '模型 B'
        assert {x['id'] for x in client.get('/api/v1/model-configs').json()} == {a['id'], new['id']}
        stopped = client.patch(f"/api/v1/admin/model-configs/{new['id']}", json={'enabled': False})
        assert stopped.status_code == 200, stopped.text
        assert [x['id'] for x in client.get('/api/v1/model-configs').json()] == [a['id']]
        response = client.get('/api/v1/admin/model-configs')
        assert 'secret-first' not in response.text and 'secret-second' not in response.text
        assert 'ciphertext' not in response.text and 'api_key' not in response.text


@pytest.mark.parametrize('adapter', ['openai', 'anthropic'])
def test_direct_models_use_the_selected_http_protocol(tmp_path, adapter):
    with model_server() as upstream, _client(tmp_path) as client:
        client.post('/api/v1/admin/bootstrap', json={'username': 'admin', 'password': 'Admin-password-123'})
        _login(client, 'admin', 'Admin-password-123')
        config = client.post('/api/v1/admin/model-configs', json={
            'display_name': '直接模型', 'model_name': 'selected-model', 'adapter': adapter,
            'base_url': upstream.base_url, 'api_key': 'boundary-key',
            'parameters': {'temperature': 0.4, 'model': 'wrong-model', 'messages': [], 'system': 'wrong-system'},
        }).json()
        work = client.post('/api/v1/works', json={'title': '协议测试'}).json()['id']
        response = client.post(f'/api/v1/works/{work}/conversations', json={'message': '讨论故事', 'model_config_version_id': config['id']})
        assert response.status_code == 201, response.text
        sent = upstream.requests[0]
        assert sent['body']['model'] == 'selected-model' and sent['body']['temperature'] == 0.4
        if adapter == 'openai':
            assert sent['path'] == '/v1/chat/completions'
            assert sent['authorization'] == 'Bearer boundary-key'
        else:
            assert sent['path'] == '/v1/messages'
            assert sent['x-api-key'] == 'boundary-key'
            assert 'wrong-system' not in sent['body']['system']
        assert 'boundary-key' not in response.text


def test_direct_conversation_freezes_only_selected_context_and_allows_editing(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    with model_server() as upstream, _client(tmp_path) as client:
        client.post('/api/v1/admin/bootstrap', json={'username': 'admin', 'password': 'Admin-password-123'})
        _login(client, 'admin', 'Admin-password-123')
        config = client.post('/api/v1/admin/model-configs', json={'model_name': 'hold-context', 'base_url': upstream.base_url, 'api_key': 'test'}).json()
        work = client.post('/api/v1/works', json={'title': '上下文'}).json()['id']
        content = {'episodes': [{'number': 1, 'title': '第一集', 'scenes': [{'number': 1, 'fragments': [{'id': 'one', 'blocks': [{'type': 'ACTION', 'text': '正文独特标记'}]}]}]}]}
        assert client.patch(f'/api/v1/works/{work}/draft', json={'content': content, 'expected_revision': 0}).status_code == 200
        body = {'message': '讨论角色', 'model_config_version_id': config['id'], 'idempotency_key': 'same-operation'}
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(client.post, f'/api/v1/works/{work}/conversations', json=body)
            # A missing direct-call contract must fail promptly, never hang the test.
            if not upstream.started.wait(3):
                response = pending.result(timeout=3)
                assert response.status_code == 201, response.text
            try:
                duplicate = client.post(f'/api/v1/works/{work}/conversations', json=body)
                assert duplicate.status_code == 201 and duplicate.json()['status'] in {'RUNNING', 'QUEUED'}
                busy = client.post(f'/api/v1/works/{work}/conversations', json={**body, 'idempotency_key': 'another'})
                assert busy.status_code == 409 and busy.json()['detail']['code'] == 'AI_ALREADY_RUNNING'
                assert client.patch(f'/api/v1/works/{work}/draft', json={'content': {}, 'expected_revision': 1}).status_code == 200
            finally:
                upstream.release.set()
            response = pending.result(timeout=5)
        assert response.status_code == 201, response.text
        assert response.json()['status'] == 'SUCCEEDED'
        assert response.json()['model_config_version_id'] == config['id']
        assert len(upstream.requests) == 1
        assert '正文独特标记' not in str(upstream.requests[0]['body'])
        assert client.get(f'/api/v1/works/{work}/versions').json() == []
        assert client.get(f'/api/v1/works/{work}/draft').json()['content'] == {}
