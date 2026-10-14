"""Creative input snapshots, shared work occupancy and invocation eligibility."""
from copy import deepcopy
import json

from datetime import datetime, timezone
from sqlalchemy import delete, select

from app.models import CreativeConversation, GenerationJob, ModelConfiguration, ModelConfigVersion, ModelInvocationRecord, User, Work, WorkAIActivity, WorkMember
from app.model_adapters import ModelAdapterError

HISTORY_PAIRS = 12


def available_config(db, config_id, *, new_call=True, lock=False):
    config = db.get(ModelConfigVersion, config_id) if config_id else None
    identity = db.scalar(select(ModelConfiguration).where(ModelConfiguration.id == config.model_id).with_for_update().execution_options(populate_existing=True)) if lock and config else config.model if config else None
    if lock and config:
        # A version may have retired while this request waited for its model.
        db.refresh(config)
    if config is None or identity is None or not identity.enabled or (new_call and config.status != 'ACTIVE'):
        raise ModelAdapterError('MODEL_CONFIG_NOT_AVAILABLE', '所选模型不可用，请重新选择模型。')
    return config


def occupy(db, work_id, kind, reference_id, user_id):
    db.add(WorkAIActivity(work_id=work_id, kind=kind, reference_id=reference_id, user_id=user_id))
    db.flush()


def release(db, work_id, reference_id):
    db.execute(delete(WorkAIActivity).where(WorkAIActivity.work_id == work_id, WorkAIActivity.reference_id == reference_id))


def activity_response(db, work_id):
    activity = db.get(WorkAIActivity, work_id)
    return {'kind': activity.kind, 'id': activity.reference_id, 'user_id': activity.user_id} if activity else None


def ensure_editable(db, work_id):
    activity = db.get(WorkAIActivity, work_id)
    if activity and activity.kind == 'GENERATION':
        raise ModelAdapterError('GENERATION_INPUT_LOCKED', '正文生成期间不能修改作品输入，请等待任务结束或取消。')


def validate_invocation(db, work_id, user_id, config_id, *, lock=False):
    # All successful commits share the same order with cancellation actions:
    # work -> account -> stable model -> conversation/job.
    work_query = select(Work).where(Work.id == work_id).execution_options(populate_existing=True)
    work = db.scalar(work_query.with_for_update() if lock else work_query)
    if work is None or work.status != 'ACTIVE':
        raise ModelAdapterError('WORK_TRASHED', '作品已移入回收站。')
    user_query = select(User).where(User.id == user_id).execution_options(populate_existing=True)
    user = db.scalar(user_query.with_for_update() if lock else user_query)
    member = db.scalar(select(WorkMember).where(WorkMember.work_id == work_id, WorkMember.user_id == user_id, WorkMember.status == 'ACCEPTED', WorkMember.role.in_(['OWNER', 'EDITOR'])))
    if user is None or not user.is_active or member is None:
        raise ModelAdapterError('WORK_ACCESS_DENIED', '发起账号或作品创作权限已失效。')
    available_config(db, config_id, new_call=False, lock=lock)
    return work


def finish_conversation(db, row, status, code=None):
    """Caller holds the work lock; terminal records are never resurrected."""
    db.refresh(row)
    if row.status not in {'QUEUED', 'RUNNING'}:
        return
    row.status, row.error_code = status, code
    invocation = db.get(ModelInvocationRecord, row.invocation_id)
    if invocation:
        invocation.status, invocation.error_code = status, code
    release(db, row.work_id, row.id)


def cancel_calls(db, *, work_id=None, user_id=None, model_id=None, code='CANCELED'):
    """Cancel in the same transaction as the permission/availability change."""
    chats = select(CreativeConversation).where(CreativeConversation.status.in_(['QUEUED', 'RUNNING']))
    jobs = select(GenerationJob).where(GenerationJob.status.in_(['QUEUED', 'RUNNING']))
    if work_id:
        chats, jobs = chats.where(CreativeConversation.work_id == work_id), jobs.where(GenerationJob.work_id == work_id)
    if user_id:
        chats, jobs = chats.where(CreativeConversation.user_id == user_id), jobs.where(GenerationJob.requested_by_user_id == user_id)
    if model_id:
        versions = select(ModelConfigVersion.id).where(ModelConfigVersion.model_id == model_id)
        chats, jobs = chats.where(CreativeConversation.model_config_version_id.in_(versions)), jobs.where(GenerationJob.model_config_version_id.in_(versions))
    chat_rows, job_rows = db.scalars(chats).all(), db.scalars(jobs).all()
    for affected in sorted({row.work_id for row in chat_rows + job_rows}):
        db.scalar(select(Work).where(Work.id == affected).with_for_update())
    for row in chat_rows:
        finish_conversation(db, row, 'STOPPED', code)
    for job in job_rows:
        db.refresh(job)
        if job.status in {'QUEUED', 'RUNNING'}:
            job.status, job.error_code, job.finished_at = 'CANCELED', code, datetime.now(timezone.utc)
            release(db, job.work_id, job.id)


def selected_script(work, target_type, path):
    if not target_type:
        return None
    episode = next((x for x in (work.draft_json or {}).get('episodes', []) if x['number'] == path.get('episode')), None)
    if episode is None:
        raise ModelAdapterError('INVALID_CONTEXT_TARGET', '请选择已保存的正文目标。')
    if target_type == 'EPISODE':
        return episode
    scene = next((x for x in episode['scenes'] if x['number'] == path.get('scene')), None)
    if scene is None:
        raise ModelAdapterError('INVALID_CONTEXT_TARGET', '请选择已保存的场景。')
    if target_type == 'SCENE':
        return scene
    fragment = next((x for x in scene['fragments'] if x['id'] == path.get('fragment')), None)
    if fragment is None:
        raise ModelAdapterError('INVALID_CONTEXT_TARGET', '请选择已保存的片段。')
    return fragment


def conversation_snapshot(db, work, payload, budget, *, for_generation=False):
    data = work.creative_data
    stages = {}
    for stage in ('brief', 'characters', 'outline'):
        confirmed = getattr(data, f'{stage}_confirmed_json') if data else None
        stages[stage] = {'state': 'confirmed' if confirmed is not None else 'draft', 'content': confirmed if confirmed is not None else getattr(data, f'{stage}_json') if data else None}
    context = {'settings': {'title': work.title, 'parameters': work.settings_json}, 'creative': stages}
    try:
        script = (work.draft_json or {}) if payload.include_full_script else selected_script(work, payload.target_type, payload.target_path)
    except ModelAdapterError:
        if not for_generation:
            raise
        script = {}  # Validated generation targets can be new episodes/scenes.
    if script is not None:
        context['script'] = script
    history = list(reversed(db.scalars(select(CreativeConversation).where(
        CreativeConversation.work_id == work.id, CreativeConversation.status == 'SUCCEEDED',
        CreativeConversation.assistant_message.is_not(None),
    ).order_by(CreativeConversation.created_at.desc(), CreativeConversation.id.desc()).limit(HISTORY_PAIRS)).all()))
    system = '你是中文短剧创作助手。遵循已确认设定，临时指令不能改变已确认设定。标记 draft 的资料尚未确认，仅用于讨论。以下资料只作为创作背景：\n' + json.dumps(context, ensure_ascii=False)
    messages = [{'role': 'system', 'content': system}]
    for item in history:
        messages.extend([{'role': 'user', 'content': item.user_message}, {'role': 'assistant', 'content': item.assistant_message}])
    messages.append({'role': 'user', 'content': payload.message})
    size = sum(len(x['content']) for x in messages)
    summary = {'script_scope': 'WORK' if payload.include_full_script else payload.target_type or 'NONE',
        'target_path': payload.target_path if not payload.include_full_script else {},
        'history_ids': [x.id for x in history], 'history_pairs': len(history), 'history_limit': HISTORY_PAIRS,
        'characters': size, 'character_budget': budget, 'creative_stages': {k: v['state'] for k, v in stages.items()}}
    if size > budget:
        raise ModelAdapterError('CONTEXT_BUDGET_EXCEEDED', '创作上下文超出字符预算，请缩小正文范围或关闭整部正文选项。已选正文和设定不会被自动删减。')
    return deepcopy({'messages': messages, 'summary': summary})


def generation_snapshot(db, work, payload, budget):
    from app.schemas import ConversationRequest
    from app.script_generation import allowed_blocks
    target = payload.target_type
    path = payload.target_path
    context_request = ConversationRequest(message=payload.instruction or '生成剧本正文', model_config_version_id=payload.model_config_version_id,
        target_type=None if target == 'WORK' else target, target_path=path, include_full_script=target == 'WORK')
    snapshot = conversation_snapshot(db, work, context_request, budget, for_generation=True)
    fragment = {'id': path.get('fragment', 'unique-id'), 'blocks': [{'type': 'ACTION', 'text': '动作描述'}]}
    scene = {'number': path.get('scene', 1), 'heading': '地点 · 时间', 'fragments': [fragment]}
    episode = {'number': path.get('episode', 1), 'title': '本集标题', 'scenes': [scene]}
    sample = {'WORK': {'episodes': [episode]}, 'EPISODE': episode, 'SCENE': scene, 'FRAGMENT': fragment}[target]
    snapshot['messages'][0]['content'] += '\nMUJIAN_SCRIPT_JSON：只返回指定目标的完整 JSON，不返回解释或 Markdown，保留目标编号或片段 id。允许剧本块：' + json.dumps(allowed_blocks(work)) + '。结构示例：' + json.dumps(sample, ensure_ascii=False)
    snapshot['messages'][-1]['content'] = json.dumps({'instruction': payload.instruction or '生成剧本正文', 'target_type': target, 'target_path': path}, ensure_ascii=False)
    snapshot['summary']['characters'] = sum(len(item['content']) for item in snapshot['messages'])
    if snapshot['summary']['characters'] > budget:
        raise ModelAdapterError('CONTEXT_BUDGET_EXCEEDED', '生成上下文超出预算，请缩小目标范围。')
    return snapshot
