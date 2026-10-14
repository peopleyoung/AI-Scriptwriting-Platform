from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

import pytest

from http_model_server import model_server
from test_versions_generation_export import _client, _login, _confirm_all


@pytest.fixture
def direct_studio(tmp_path):
    with model_server() as upstream, _client(tmp_path) as client:
        client.post('/api/v1/admin/bootstrap', json={'username': 'admin', 'password': 'Admin-password-123'})
        _login(client, 'admin', 'Admin-password-123')
        model = client.post('/api/v1/admin/model-configs', json={'display_name': '模型A', 'model_name': 'hold-model', 'base_url': upstream.base_url, 'api_key': 'old-key'}).json()
        work = client.post('/api/v1/works', json={'title': '生命周期'}).json()['id']
        yield client, upstream, model, work


def wait_message(client, work, predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        rows = client.get(f'/api/v1/works/{work}/conversations').json()
        if rows and predicate(rows[-1]):
            return rows[-1]
        Event().wait(.03)
    pytest.fail('conversation did not reach the expected public state')


def test_stop_persists_partial_text_never_commits_success_and_excludes_incomplete_history(direct_studio):
    client, upstream, model, work = direct_studio
    body = {'message': '应该排除的半轮问题', 'model_config_version_id': model['id'], 'idempotency_key': 'stop-once'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        stream = pool.submit(client.post, f'/api/v1/works/{work}/conversations/stream', json=body)
        assert upstream.started.wait(3)
        row = wait_message(client, work, lambda row: row['assistant_message'] == '第一段回复')
        stopped = client.post(f"/api/v1/works/{work}/conversations/{row['id']}/stop")
        assert stopped.status_code == 200, stopped.text
        assert stopped.json()['status'] == 'STOPPED'
        upstream.release.set()
        assert stream.result(timeout=5).status_code == 200
    saved = client.get(f'/api/v1/works/{work}/conversations').json()[0]
    assert saved['status'] == 'STOPPED' and saved['assistant_message'] == '第一段回复'
    assert client.get(f'/api/v1/works/{work}/ai-activity').json() is None
    # A network resend replays the existing record without a second HTTP call.
    duplicate = client.post(f'/api/v1/works/{work}/conversations', json=body)
    assert duplicate.status_code == 201 and len(upstream.requests) == 1
    retry = client.post(f'/api/v1/works/{work}/conversations', json={**body, 'message': '新的讨论', 'idempotency_key': 'manual-retry'})
    assert retry.status_code == 201, retry.text
    assert len(upstream.requests) == 2
    assert '应该排除的半轮问题' not in str(upstream.requests[-1]['body'])
    assert len(client.get(f'/api/v1/works/{work}/conversations').json()) == 2


@pytest.mark.parametrize('kind', ['chat', 'generation'])
def test_config_update_pins_running_call_but_disabling_stops_all_versions(direct_studio, kind):
    client, upstream, model, work = direct_studio
    if kind == 'generation':
        _confirm_all(client, work)
        job = client.post(f'/api/v1/works/{work}/generation-jobs', json={'model_config_version_id': model['id'], 'target_type': 'EPISODE', 'target_path': {'episode': 1}})
        assert job.status_code == 201, job.text
        path = f"/api/v1/works/{work}/generation-jobs/{job.json()['id']}/run"
        body = None
    else:
        path = f'/api/v1/works/{work}/conversations'
        body = {'model_config_version_id': model['id'], 'message': '更新期间的对话'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, path, **({'json': body} if body else {}))
        assert upstream.started.wait(3)
        changed = client.patch(f"/api/v1/admin/model-configs/{model['id']}", json={'api_key': 'new-key', 'model_name': 'new-model'})
        assert changed.status_code == 200, changed.text
        assert upstream.requests[0]['authorization'] == 'Bearer old-key'
        if kind == 'generation':
            locked = client.patch(f'/api/v1/works/{work}/draft', json={'content': {}})
            assert locked.status_code == 409 and locked.json()['detail']['code'] == 'GENERATION_INPUT_LOCKED'
        upstream.release.set()
        response = pending.result(timeout=5)
        assert response.json()['status'] == 'SUCCEEDED', response.text
    new = changed.json()
    followup = client.post(f'/api/v1/works/{work}/conversations', json={'model_config_version_id': new['id'], 'message': '使用新版本'})
    assert followup.status_code == 201, followup.text
    assert upstream.requests[-1]['authorization'] == 'Bearer new-key'
    # Start a call pinned to a now-retired version, then disable the identity.
    holding = client.patch(f"/api/v1/admin/model-configs/{new['id']}", json={'model_name': 'hold-retired'}).json()
    upstream.started.clear(); upstream.release.clear()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, f'/api/v1/works/{work}/conversations', json={'message': '停用进行中的版本', 'model_config_version_id': holding['id']})
        assert upstream.started.wait(3)
        latest = client.patch(f"/api/v1/admin/model-configs/{holding['id']}", json={'display_name': '最新名称'}).json()
        disabled = client.patch(f"/api/v1/admin/model-configs/{latest['id']}", json={'enabled': False})
        assert disabled.status_code == 200, disabled.text
        assert client.get(f'/api/v1/works/{work}/ai-activity').json() is None
        upstream.release.set()
        assert pending.result(timeout=5).json()['status'] == 'STOPPED'
    assert client.get(f'/api/v1/works/{work}/conversations').json()[-1]['status'] == 'STOPPED'
    rejected = client.post(f'/api/v1/works/{work}/conversations', json={'message': '不能继续发送', 'model_config_version_id': latest['id']})
    assert rejected.status_code == 409


def test_generation_uses_only_target_and_preserves_other_content(direct_studio):
    from copy import deepcopy
    client, upstream, model, work = direct_studio
    _confirm_all(client, work)
    draft = {'episodes': [{'number': n, 'scenes': [{'number': 1, 'fragments': [{'id': f'f{n}', 'blocks': [{'type': 'ACTION', 'text': f'独特正文{n}'}]}]}]} for n in [1, 2]]}
    client.patch(f'/api/v1/works/{work}/draft', json={'content': draft})
    upstream.release.set()
    response = client.post(f'/api/v1/works/{work}/generation-jobs', json={'model_config_version_id': model['id'], 'target_type': 'FRAGMENT', 'target_path': {'episode': 1, 'scene': 1, 'fragment': 'f1'}, 'instruction': '重写动作'})
    assert response.status_code == 201, response.text
    job_id = response.json()['id']
    done = client.post(f'/api/v1/works/{work}/generation-jobs/{job_id}/run')
    assert done.json()['status'] == 'SUCCEEDED', done.text
    assert '独特正文1' in str(upstream.requests[0]['body']) and '独特正文2' not in str(upstream.requests[0]['body'])
    expected = deepcopy(draft)
    expected['episodes'][0]['scenes'][0]['fragments'][0]['blocks'] = [{'type': 'ACTION', 'text': 'HTTP生成正文'}]
    assert client.get(f'/api/v1/works/{work}/draft').json()['content'] == expected
    assert client.get(f'/api/v1/works/{work}/versions').json()[0]['content'] == expected
    assert client.get(f'/api/v1/works/{work}/ai-activity').json() is None


@pytest.mark.parametrize('reason', ['member', 'account', 'trash', 'viewer'])
def test_permission_invalidation_stops_inflight_conversation(direct_studio, reason):
    from test_versions_generation_export import CsrfClient
    owner, upstream, model, work = direct_studio
    user = owner.post('/api/v1/admin/users', json={'username': 'editor', 'password': 'Editor-password-123', 'role': 'WRITER'}).json()
    assert owner.post(f'/api/v1/works/{work}/members/invitations', json={'user_id': user['id'], 'role': 'EDITOR'}).status_code == 201
    editor = CsrfClient(owner.app)
    _login(editor, 'editor', 'Editor-password-123')
    assert editor.post(f'/api/v1/works/{work}/members/accept').status_code == 200
    body = {'model_config_version_id': model['id'], 'message': '失效中的讨论'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(editor.post, f'/api/v1/works/{work}/conversations', json=body)
        assert upstream.started.wait(3)
        wait_message(owner, work, lambda row: row['assistant_message'] == '第一段回复')
        if reason == 'member':
            action = owner.post(f"/api/v1/works/{work}/members/{user['id']}/revoke")
        elif reason == 'account':
            action = owner.patch(f"/api/v1/admin/users/{user['id']}/status", json={'active': False})
        elif reason == 'viewer':
            action = owner.patch(f"/api/v1/works/{work}/members/{user['id']}", json={'role': 'VIEWER'})
        else:
            action = owner.post(f'/api/v1/works/{work}/trash')
        assert action.status_code in {200, 204}, action.text
        upstream.release.set()
        assert pending.result(timeout=5).json()['status'] == 'STOPPED'
    if reason == 'trash':
        assert editor.post(f'/api/v1/works/{work}/conversations', json=body).status_code == 404
        assert owner.post(f'/api/v1/works/{work}/restore').status_code == 200
    assert owner.get(f'/api/v1/works/{work}/ai-activity').json() is None
    assert owner.get(f'/api/v1/works/{work}/conversations').json()[-1]['status'] == 'STOPPED'
    if reason != 'trash':
        assert editor.post(f'/api/v1/works/{work}/conversations', json=body).status_code in {401, 403, 404}


def test_context_scope_budget_and_cross_model_history(direct_studio):
    client, upstream, model, work = direct_studio
    upstream.release.set()
    draft = {'episodes': [{'number': 1, 'scenes': [{'number': n, 'fragments': [{'id': f'f{n}', 'blocks': [{'type': 'ACTION', 'text': f'范围标记{n}'}]}]} for n in [1, 2]]}]}
    client.patch(f'/api/v1/works/{work}/draft', json={'content': draft})
    body = {'model_config_version_id': model['id'], 'message': '第一轮完整问题'}
    for scope, path, present in [(None, {}, []), ('FRAGMENT', {'episode': 1, 'scene': 1, 'fragment': 'f1'}, [1]), ('SCENE', {'episode': 1, 'scene': 2}, [2]), ('EPISODE', {'episode': 1}, [1, 2])]:
        request = {**body, 'target_type': scope, 'target_path': path}
        summary = client.post(f'/api/v1/works/{work}/conversation-context', json=request)
        assert summary.status_code == 200, summary.text
        response = client.post(f'/api/v1/works/{work}/conversations', json=request)
        assert response.status_code == 201, response.text
        assert response.json()['context_summary'] == summary.json()
        sent = upstream.requests[-1]['body']['messages'][0]['content']
        for number in [1, 2]:
            assert (f'范围标记{number}' in sent) == (number in present)
    another = client.post('/api/v1/admin/model-configs', json={'model_name': 'other', 'base_url': upstream.base_url, 'api_key': 'other'}).json()
    response = client.post(f'/api/v1/works/{work}/conversations', json={**body, 'message': '换模型后讨论', 'model_config_version_id': another['id'], 'include_full_script': True})
    assert response.status_code == 201
    assert '第一轮完整问题' in str(upstream.requests[-1]['body'])
    assert response.json()['context_summary']['history_pairs'] == 4
    draft['episodes'][0]['scenes'][0]['fragments'][0]['blocks'][0]['text'] = '长' * 61000
    client.patch(f'/api/v1/works/{work}/draft', json={'content': draft})
    count = len(upstream.requests)
    too_large = client.post(f'/api/v1/works/{work}/conversations', json={**body, 'include_full_script': True})
    assert too_large.status_code == 422 and too_large.json()['detail']['code'] == 'CONTEXT_BUDGET_EXCEEDED'
    assert len(upstream.requests) == count


def test_stop_closes_upstream_even_while_waiting_for_response_headers(direct_studio):
    client, upstream, model, work = direct_studio
    config = client.patch(f"/api/v1/admin/model-configs/{model['id']}", json={'model_name': 'headers-hold'}).json()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, f'/api/v1/works/{work}/conversations', json={'message': '等待响应头', 'model_config_version_id': config['id']})
        try:
            assert upstream.started.wait(3)
            row = wait_message(client, work, lambda row: row['status'] == 'RUNNING')
            stopped = client.post(f"/api/v1/works/{work}/conversations/{row['id']}/stop")
            assert stopped.status_code == 200
            assert upstream.disconnected.wait(2), 'stop must close the actual upstream connection before headers arrive'
            assert pending.result(timeout=2).json()['status'] == 'STOPPED'
        finally:
            upstream.release.set()


def test_recent_history_preserves_chronological_order_for_fast_replies(tmp_path):
    with _client(tmp_path) as client:
        client.post('/api/v1/admin/bootstrap', json={'username': 'admin', 'password': 'Admin-password-123'})
        _login(client, 'admin', 'Admin-password-123')
        config = client.post('/api/v1/admin/model-configs', json={'model_name': 'quick', 'adapter': 'mock', 'api_key': 'test'}).json()
        work = client.post('/api/v1/works', json={'title': '快速对话'}).json()['id']
        ids = []
        for number in range(6):
            response = client.post(f'/api/v1/works/{work}/conversations', json={'message': f'第{number}轮', 'model_config_version_id': config['id']})
            assert response.status_code == 201, response.text
            assert response.json()['context_summary']['history_ids'] == ids
            ids.append(response.json()['id'])
        assert [row['id'] for row in client.get(f'/api/v1/works/{work}/conversations').json()] == ids


@pytest.mark.parametrize('role', ['VIEWER', 'NONMEMBER', 'ADMIN'])
def test_readonly_and_uninvited_accounts_cannot_invoke_models(direct_studio, role):
    from test_versions_generation_export import CsrfClient
    owner, upstream, model, work = direct_studio
    _confirm_all(owner, work)
    user = owner.post('/api/v1/admin/users', json={'username': 'restricted', 'password': 'Reader-password-123', 'role': 'ADMIN' if role == 'ADMIN' else 'WRITER'}).json()
    if role == 'VIEWER':
        owner.post(f'/api/v1/works/{work}/members/invitations', json={'user_id': user['id'], 'role': 'VIEWER'})
    reader = CsrfClient(owner.app)
    _login(reader, 'restricted', 'Reader-password-123')
    if role == 'VIEWER':
        assert reader.post(f'/api/v1/works/{work}/members/accept').status_code == 200
    for endpoint, body in [
        ('conversations', {'message': '不能发送'}),
        ('conversations/stream', {'message': '不能发送'}),
        ('generation-jobs', {'target_type': 'EPISODE', 'target_path': {'episode': 1}}),
        ('generation-jobs', {'target_type': 'FRAGMENT', 'target_path': {'episode': 1, 'scene': 1, 'fragment': 'f'}}),
    ]:
        denied = reader.post(f'/api/v1/works/{work}/{endpoint}', json={**body, 'model_config_version_id': model['id']})
        assert denied.status_code == 403, denied.text
    assert reader.get(f'/api/v1/works/{work}/conversations').status_code == (200 if role == 'VIEWER' else 403)
    assert reader.post('/api/v1/admin/model-configs', json={'model_name': 'x', 'api_key': 'x'}).status_code == (201 if role == 'ADMIN' else 403)
    assert upstream.requests == []


@pytest.mark.parametrize('queued', [True, False])
@pytest.mark.parametrize('reason', ['member', 'account', 'trash', 'model'])
def test_invalidation_cancels_generation_and_releases_input_lock(direct_studio, queued, reason):
    from test_versions_generation_export import CsrfClient
    owner, upstream, model, work = direct_studio
    _confirm_all(owner, work)
    user = owner.post('/api/v1/admin/users', json={'username': 'job-editor', 'password': 'Editor-password-123', 'role': 'WRITER'}).json()
    owner.post(f'/api/v1/works/{work}/members/invitations', json={'user_id': user['id'], 'role': 'EDITOR'})
    editor = CsrfClient(owner.app)
    _login(editor, 'job-editor', 'Editor-password-123')
    editor.post(f'/api/v1/works/{work}/members/accept')
    job = editor.post(f'/api/v1/works/{work}/generation-jobs', json={'model_config_version_id': model['id'], 'target_type': 'EPISODE', 'target_path': {'episode': 1}}).json()
    assert owner.patch(f'/api/v1/works/{work}/draft', json={'content': {}}).status_code == 409
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = None if queued else pool.submit(editor.post, f"/api/v1/works/{work}/generation-jobs/{job['id']}/run")
        try:
            if pending:
                assert upstream.started.wait(3)
            if reason == 'member':
                action = owner.post(f"/api/v1/works/{work}/members/{user['id']}/revoke")
            elif reason == 'account':
                action = owner.patch(f"/api/v1/admin/users/{user['id']}/status", json={'active': False})
            elif reason == 'model':
                action = owner.patch(f"/api/v1/admin/model-configs/{model['id']}", json={'enabled': False})
            else:
                action = owner.post(f'/api/v1/works/{work}/trash')
            assert action.status_code in {200, 204}, action.text
            assert pending is None or pending.result(timeout=3).json()['status'] in {'CANCELED', 'FAILED'}
        finally:
            upstream.release.set()
    if reason == 'trash':
        owner.post(f'/api/v1/works/{work}/restore')
    state = owner.get(f"/api/v1/works/{work}/generation-jobs/{job['id']}").json()
    assert state['status'] in {'CANCELED', 'FAILED'} and state['result_version_id'] is None
    assert owner.get(f'/api/v1/works/{work}/versions').json() == []
    assert owner.get(f'/api/v1/works/{work}/ai-activity').json() is None
    assert owner.patch(f'/api/v1/works/{work}/draft', json={'content': {}}).status_code == 200
    if queued:
        assert owner.post(f"/api/v1/works/{work}/generation-jobs/{job['id']}/run").json()['status'] == 'CANCELED'
        assert upstream.requests == []


def test_different_works_can_chat_concurrently(direct_studio):
    client, upstream, model, first = direct_studio
    second = client.post('/api/v1/works', json={'title': '另一部作品'}).json()['id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(client.post, f'/api/v1/works/{work}/conversations', json={'message': '分别讨论', 'model_config_version_id': model['id']}) for work in [first, second]]
        try:
            deadline = time.monotonic() + 4
            while len(upstream.requests) != 2:
                assert time.monotonic() < deadline
                Event().wait(.03)
            assert client.get(f'/api/v1/works/{first}/ai-activity').json() is not None
            assert client.get(f'/api/v1/works/{second}/ai-activity').json() is not None
        finally:
            upstream.release.set()
        assert all(call.result(timeout=5).json()['status'] == 'SUCCEEDED' for call in calls)
