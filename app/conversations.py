"""One durable conversation lifecycle, exposed as JSON or incremental SSE."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import time
from threading import Thread
from uuid import uuid4

from fastapi import Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.creative_calls import available_config, conversation_snapshot, finish_conversation, occupy, validate_invocation
from app.crypto import decrypt_secret
from app.db import get_db
from app.model_adapters import ModelAdapterError, adapter_for
from app.models import CreativeConversation, ModelInvocationRecord, Work
from app.schemas import ConversationRequest, ConversationResponse


def conversation_response(row):
    config = row.config
    return ConversationResponse(
        id=row.id, work_id=row.work_id, user_id=row.user_id,
        user_message=row.user_message, assistant_message=row.assistant_message,
        status=row.status, invocation_id=row.invocation_id,
        model_config_version_id=row.model_config_version_id,
        model_id=config.model_id if config else None,
        model_display_name=(config.display_name or config.model_name) if config else None,
        model_version=config.version if config else None, input_revision=row.input_revision,
        context_summary=(row.input_snapshot_json or {}).get('summary'),
        error_code=row.error_code, created_at=row.created_at,
    )


class ConversationService:
    def __init__(self, app, settings, require_member, error):
        self.app, self.settings = app, settings
        self.require_member, self.error = require_member, error

    def create(self, db, user, work_id, payload):
        work, _ = self.require_member(db, user, work_id, {'OWNER', 'EDITOR'})
        key = payload.idempotency_key or uuid4().hex
        request_hash = hashlib.sha256(json.dumps(payload.model_dump(exclude={'idempotency_key'}), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        def existing():
            row = db.scalar(select(CreativeConversation).where(CreativeConversation.work_id == work_id, CreativeConversation.idempotency_key == key))
            if row and (row.user_id != user.id or row.request_hash != request_hash):
                raise self.error('IDEMPOTENCY_CONFLICT', '该发送标识已用于另一请求。', 409)
            return row
        row = existing()
        if row:
            return row, False
        try:
            config = available_config(db, payload.model_config_version_id, lock=True)
            snapshot = conversation_snapshot(db, work, payload, self.settings.creative_context_max_characters)
        except ModelAdapterError as exc:
            raise self.error(exc.code, str(exc), 422 if exc.code in {'CONTEXT_BUDGET_EXCEEDED', 'INVALID_CONTEXT_TARGET'} else 409)
        row = CreativeConversation(id=str(uuid4()), work_id=work_id, user_id=user.id,
            user_message=payload.message, status='RUNNING', config=config,
            created_at=datetime.now(timezone.utc),
            model_config_version_id=config.id, input_revision=work.revision,
            input_snapshot_json=snapshot, idempotency_key=key, request_hash=request_hash)
        invocation = ModelInvocationRecord(work_id=work_id, provider_id=config.provider_id,
            model_config_version_id=config.id, status='RUNNING', usage_json={}, trace_id=uuid4().hex)
        db.add_all([row, invocation])
        try:
            db.flush()
            row.invocation_id = invocation.id
            occupy(db, work_id, 'CONVERSATION', row.id, user.id)
            db.commit()
        except IntegrityError:
            db.rollback()
            row = existing()
            if row:
                return row, False
            raise self.error('AI_ALREADY_RUNNING', '作品已有成员正在调用模型，当前输入已保留。', 409)
        return row, True

    def finish(self, conversation_id, status, code):
        with self.app.state.session_factory() as db:
            row = db.get(CreativeConversation, conversation_id)
            if row:
                db.scalar(select(Work).where(Work.id == row.work_id).with_for_update())
                finish_conversation(db, row, status, code)
                db.commit()

    def execute(self, conversation_id):
        started = time.monotonic()
        service = self
        class Token:
            @property
            def cancelled(self):
                if service.app.state.generation_worker.stopping.is_set():
                    return True
                with service.app.state.session_factory() as db:
                    row = db.get(CreativeConversation, conversation_id)
                    if row is None or row.status != 'RUNNING':
                        return True
                    if time.monotonic() - started > service.settings.model_timeout_seconds:
                        raise ModelAdapterError('TIMEOUT', '模型回复超时。')
                    validate_invocation(db, row.work_id, row.user_id, row.model_config_version_id)
                return False
        try:
            with self.app.state.session_factory() as db:
                row = db.get(CreativeConversation, conversation_id)
                validate_invocation(db, row.work_id, row.user_id, row.model_config_version_id)
                config = row.config
                messages = deepcopy(row.input_snapshot_json['messages'])
                model, parameters = config.model_name, deepcopy(config.parameters_json or {})
                adapter = adapter_for(config.adapter or config.provider.adapter, endpoint=config.endpoint,
                    credential=decrypt_secret(config.credential_ciphertext, self.settings.external_encryption_key))
            token = Token()
            for chunk in adapter.stream(messages=messages, model=model, parameters=parameters, timeout=self.settings.model_timeout_seconds, cancel_token=token):
                if token.cancelled:
                    raise ModelAdapterError('CANCELED', '对话已停止。')
                with self.app.state.session_factory() as db:
                    row = db.get(CreativeConversation, conversation_id)
                    validate_invocation(db, row.work_id, row.user_id, row.model_config_version_id, lock=True)
                    db.refresh(row)
                    if row.status != 'RUNNING':
                        return
                    row.assistant_message = (row.assistant_message or '') + chunk
                    db.commit()
            with self.app.state.session_factory() as db:
                row = db.get(CreativeConversation, conversation_id)
                validate_invocation(db, row.work_id, row.user_id, row.model_config_version_id, lock=True)
                finish_conversation(db, row, 'SUCCEEDED')
                invocation = db.get(ModelInvocationRecord, row.invocation_id)
                if invocation and invocation.status == 'SUCCEEDED':
                    invocation.usage_json = getattr(adapter, 'last_response', None).usage if getattr(adapter, 'last_response', None) else {}
                    invocation.duration_ms = int((time.monotonic() - started) * 1000)
                db.commit()
        except Exception as exc:
            code = exc.code if isinstance(exc, ModelAdapterError) else 'MODEL_INVOCATION_FAILED'
            status = 'STOPPED' if code in {'CANCELED', 'WORK_TRASHED', 'WORK_ACCESS_DENIED', 'MODEL_CONFIG_NOT_AVAILABLE'} else 'INCOMPLETE' if code == 'INCOMPLETE_MODEL_RESPONSE' else 'FAILED'
            self.finish(conversation_id, status, code)

    async def events(self, conversation_id, initial):
        last = None
        try:
            yield 'data: ' + initial.model_dump_json() + '\n\n'
            while True:
                with self.app.state.session_factory() as db:
                    row = db.get(CreativeConversation, conversation_id)
                    response = conversation_response(row)
                serialized = response.model_dump_json()
                if serialized != last:
                    yield 'data: ' + serialized + '\n\n'
                    last = serialized
                if response.status not in {'QUEUED', 'RUNNING'}:
                    return
                await asyncio.sleep(0.05)
        finally:
            # Starlette cancels this iterator when its browser connection closes.
            # The producer sees the persisted terminal state and closes upstream.
            self.finish(conversation_id, 'INCOMPLETE', 'CLIENT_DISCONNECTED')


def register_conversation_routes(app, *, settings, active_user, check_csrf, require_member, error):
    service = ConversationService(app, settings, require_member, error)

    @app.post('/api/v1/works/{work_id}/conversation-context')
    def preview(work_id: str, payload: ConversationRequest, request: Request, user=Depends(active_user), db=Depends(get_db)):
        check_csrf(request)
        work, _ = require_member(db, user, work_id)
        try:
            return conversation_snapshot(db, work, payload, settings.creative_context_max_characters)['summary']
        except ModelAdapterError as exc:
            raise error(exc.code, str(exc), 422)

    @app.post('/api/v1/works/{work_id}/conversations', response_model=ConversationResponse, status_code=201)
    def create(work_id: str, payload: ConversationRequest, request: Request, user=Depends(active_user), db=Depends(get_db)):
        check_csrf(request)
        row, created = service.create(db, user, work_id, payload)
        row_id = row.id
        if created:
            db.rollback()
            service.execute(row_id)
            db.expire_all()
            row = db.get(CreativeConversation, row_id)
            if row.status in {'FAILED', 'INCOMPLETE'}:
                raise error(row.error_code or 'MODEL_INVOCATION_FAILED', '对话未完成，可手动重试。', 502)
        return conversation_response(row)

    @app.post('/api/v1/works/{work_id}/conversations/stream')
    def stream(work_id: str, payload: ConversationRequest, request: Request, user=Depends(active_user), db=Depends(get_db)):
        check_csrf(request)
        row, created = service.create(db, user, work_id, payload)
        initial = conversation_response(row)
        row_id = row.id
        # Replayed requests also acquired a work lock during authorization.
        # A long-lived response must never carry that transaction with it.
        db.rollback()
        if created:
            Thread(target=service.execute, args=(row_id,), daemon=True, name='mujian-conversation').start()
        return StreamingResponse(service.events(row_id, initial), media_type='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.post('/api/v1/works/{work_id}/conversations/{conversation_id}/stop', response_model=ConversationResponse)
    def stop(work_id: str, conversation_id: str, request: Request, user=Depends(active_user), db=Depends(get_db)):
        check_csrf(request)
        require_member(db, user, work_id, {'OWNER', 'EDITOR'})
        row = db.scalar(select(CreativeConversation).where(CreativeConversation.id == conversation_id, CreativeConversation.work_id == work_id))
        if row is None:
            raise error('CONVERSATION_NOT_FOUND', '对话不存在。', 404)
        finish_conversation(db, row, 'STOPPED', 'CANCELED_BY_USER')
        db.commit()
        return conversation_response(row)
