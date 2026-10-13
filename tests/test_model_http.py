import json
from types import SimpleNamespace

import httpx
import pytest

from app.crypto import encrypt_bytes, decrypt_bytes, encrypt_secret, decrypt_secret
from app.model_adapters import adapter_for, ModelAdapterError


@pytest.mark.parametrize('provider', ['openai', 'anthropic'])
@pytest.mark.parametrize('stream', [True, False])
def test_provider_contracts(provider, stream):
    requests = []
    def transport(request):
        requests.append(request)
        if provider == 'openai':
            events = [{'choices': [{'delta': {'content': '中文'}, 'finish_reason': None}]}, {'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'secret': 'must not log'}}]
            payload = {'choices': [{'message': {'content': '中文'}, 'finish_reason': 'stop'}], 'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'secret': 'must not log'}}
        else:
            events = [{'type': 'message_start', 'message': {'usage': {'input_tokens': 2}}}, {'type': 'content_block_delta', 'delta': {'text': '中文'}}, {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 1}}]
            payload = {'content': [{'type': 'text', 'text': '中文'}], 'stop_reason': 'end_turn', 'usage': {'input_tokens': 2, 'output_tokens': 1}}
        return httpx.Response(200, content=('\n\n'.join('data: '+json.dumps(e) for e in events)+'\n\n').encode() if stream else json.dumps(payload).encode(), headers={'content-type': 'text/event-stream' if stream else 'application/json'})
    adapter = adapter_for(provider, endpoint='https://test.invalid/v1', credential='credential-secret', transport=httpx.MockTransport(transport))
    result = adapter.invoke(messages=[{'role': 'system', 'content': '背景'}, {'role': 'user', 'content': '写作'}], model='model', parameters={'stream': stream, 'model': 'untrusted', 'messages': []})
    assert result.text == '中文'
    assert result.stop_reason in {'stop', 'end_turn'}
    assert sum(result.usage.values()) == 3
    assert 'secret' not in result.usage
    request = requests[0]
    body = json.loads(request.content)
    assert body['model'] == 'model' and body['stream'] == stream
    if provider == 'openai':
        assert request.url.path == '/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer credential-secret'
        assert body['messages'][0]['role'] == 'system'
    else:
        assert request.url.path == '/v1/messages'
        assert request.headers['x-api-key'] == 'credential-secret'
        assert body['system'] == '背景'
        assert body['messages'] == [{'role': 'user', 'content': '写作'}]


@pytest.mark.parametrize('status,code,retryable', [(401,'AUTHENTICATION_ERROR',False),(429,'RATE_LIMITED',True),(503,'TRANSIENT_PROVIDER_ERROR',True),(400,'INVALID_PARAMETERS',False)])
def test_safe_http_errors(status, code, retryable):
    adapter = adapter_for('openai', credential='secret', transport=httpx.MockTransport(lambda request: httpx.Response(status, text='secret prompt provider body')))
    with pytest.raises(ModelAdapterError) as failure:
        adapter.invoke(messages=[], model='m')
    assert failure.value.code == code and failure.value.retryable == retryable
    assert 'secret' not in str(failure.value)


def test_timeout_and_cancel():
    def timeout(request):
        raise httpx.ReadTimeout('secret', request=request)
    adapter = adapter_for('openai', credential='secret', transport=httpx.MockTransport(timeout))
    with pytest.raises(ModelAdapterError, match='timed out') as failure:
        adapter.invoke(messages=[], model='m')
    assert failure.value.code == 'TIMEOUT'
    with pytest.raises(ModelAdapterError) as canceled:
        adapter.invoke(messages=[], model='m', cancel_token=SimpleNamespace(cancelled=True))
    assert canceled.value.code == 'CANCELED'


def test_authenticated_encryption_tampering():
    key = 'a secure long encryption key for the test'
    first = encrypt_bytes(b'private script', key)
    assert first != encrypt_bytes(b'private script', key)
    assert decrypt_bytes(first, key) == b'private script'
    with pytest.raises(ValueError):
        decrypt_bytes(first[:-1] + bytes([first[-1] ^ 1]), key)
    with pytest.raises(ValueError):
        decrypt_bytes(first, 'wrong key')
    encrypted = encrypt_secret('secret', key)
    assert 'secret' not in encrypted and decrypt_secret(encrypted, key) == 'secret'


def test_production_requires_stable_encryption_key():
    from app.config import Settings
    with pytest.raises(ValueError, match='APP_EXTERNAL_ENCRYPTION_KEY'):
        Settings(environment='production')
    assert Settings(environment='production',external_encryption_key='a'*48).environment == 'production'
