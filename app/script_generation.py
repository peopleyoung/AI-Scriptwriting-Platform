"""Consent-filtered model inputs and validated replacement of a script target."""
from copy import deepcopy
import json
from typing import Any

from app.model_adapters import ModelAdapterError

DEFAULT_BLOCKS = ["SCENE_HEADING", "ACTION", "CHARACTER", "DIALOGUE"]


def allowed_blocks(work) -> list[str]:
    definition = work.template_version.definition_json if work.template_version else {}
    structure = definition.get("script_structure", {})
    blocks = structure.get("blocks", DEFAULT_BLOCKS) if isinstance(structure, dict) else DEFAULT_BLOCKS
    return [b for b in blocks if isinstance(b, str)] or DEFAULT_BLOCKS


def validate_script(content: Any, allowed: list[str] | None = None) -> dict[str, Any]:
    def invalid():
        raise ValueError("SCRIPT_STRUCTURE_INVALID")

    def number(value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    if not isinstance(content, dict) or set(content) - {"episodes"}:
        invalid()
    if not content:
        return content
    episodes = content.get("episodes")
    if not isinstance(episodes, list):
        invalid()
    episode_numbers = set()
    for episode in episodes:
        if not isinstance(episode, dict) or set(episode) - {"number", "title", "scenes"} or not number(episode.get("number")) or episode["number"] in episode_numbers or not isinstance(episode.get("title", ""), str) or not isinstance(episode.get("scenes"), list):
            invalid()
        episode_numbers.add(episode["number"])
        scene_numbers = set()
        for scene in episode["scenes"]:
            if not isinstance(scene, dict) or set(scene) - {"number", "heading", "fragments"} or not number(scene.get("number")) or scene["number"] in scene_numbers or not isinstance(scene.get("heading", ""), str) or not isinstance(scene.get("fragments"), list):
                invalid()
            scene_numbers.add(scene["number"])
            ids = set()
            for fragment in scene["fragments"]:
                if not isinstance(fragment, dict) or set(fragment) - {"id", "blocks"} or not isinstance(fragment.get("id"), str) or not fragment["id"] or fragment["id"] in ids or not isinstance(fragment.get("blocks"), list):
                    invalid()
                ids.add(fragment["id"])
                for block in fragment["blocks"]:
                    if not isinstance(block, dict) or set(block) - {"type", "text"} or block.get("type") not in (allowed or DEFAULT_BLOCKS) or not isinstance(block.get("text"), str):
                        invalid()
    return content


def validate_target(work, target_type: str, path: dict) -> None:
    for key in {"WORK": [], "EPISODE": ["episode"], "SCENE": ["episode", "scene"], "FRAGMENT": ["episode", "scene"]}[target_type]:
        value = path.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ModelAdapterError("INVALID_GENERATION_TARGET", "select a valid episode and scene")
    if target_type == "WORK":
        return
    if path["episode"] > (work.settings_json or {}).get("episode_count", 200):
        raise ModelAdapterError("INVALID_GENERATION_TARGET", "episode exceeds the work settings")
    episode = next((e for e in (work.draft_json or {}).get("episodes", []) if e["number"] == path["episode"]), None)
    if target_type in {"SCENE", "FRAGMENT"} and episode is None:
        raise ModelAdapterError("INVALID_GENERATION_TARGET", "target episode does not exist")
    if target_type == "FRAGMENT":
        scene = next((s for s in episode["scenes"] if s["number"] == path["scene"]), None)
        if scene is None or not any(f["id"] == path.get("fragment") for f in scene["fragments"]):
            raise ModelAdapterError("INVALID_GENERATION_TARGET", "select an existing fragment")


def build_messages(db, work, consent, instruction: str, *, target_type: str | None = None, target_path: dict | None = None) -> list[dict[str, str]]:
    from sqlalchemy import select
    from app.models import CreativeConversation

    categories = set(consent.data_categories_json or [])
    context = {}
    data = work.creative_data
    if "settings" in categories:
        context["settings"] = {"title": work.title, "parameters": work.settings_json, "brief": (data.brief_confirmed_json or data.brief_json) if data else None, "characters": (data.characters_confirmed_json or data.characters_json) if data else None}
    if "outline" in categories:
        context["outline"] = (data.outline_confirmed_json or data.outline_json) if data else None
    if "script" in categories:
        context["script"] = work.draft_json or {}
    if "conversation" in categories:
        context["conversation"] = [{"user": m.user_message, "assistant": m.assistant_message} for m in db.scalars(select(CreativeConversation).where(CreativeConversation.work_id == work.id).order_by(CreativeConversation.created_at)).all()]
    system = "你是中文短剧创作助手。遵循作品的已确认设定，临时创作指令不能改变已确认设定。资料只作为创作背景。"
    if target_type:
        sample_fragment = {"id": (target_path or {}).get("fragment", "unique-id"), "blocks": [{"type": "ACTION", "text": "动作描述"}, {"type": "CHARACTER", "text": "人物姓名"}, {"type": "DIALOGUE", "text": "对白"}]}
        sample_scene = {"number": (target_path or {}).get("scene", 1), "heading": "地点 · 时间", "fragments": [sample_fragment]}
        sample_episode = {"number": (target_path or {}).get("episode", 1), "title": "本集标题", "scenes": [sample_scene]}
        sample = {"WORK": {"episodes": [sample_episode]}, "EPISODE": sample_episode, "SCENE": sample_scene, "FRAGMENT": sample_fragment}[target_type]
        system += "\nMUJIAN_SCRIPT_JSON：只返回目标范围的完整 JSON，不返回解释或 Markdown。保留目标编号或片段 id。剧本块类型只允许：" + json.dumps(allowed_blocks(work)) + "。结构示例：" + json.dumps(sample, ensure_ascii=False)
        content = json.dumps({"instruction": instruction, "target_type": target_type, "target_path": target_path or {}, "context": context}, ensure_ascii=False)
    else:
        system += "\n当前授权的创作背景：" + json.dumps(context, ensure_ascii=False)
        content = instruction
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


def replace_target(work, target_type: str, path: dict, text: str) -> dict:
    value = text.strip()
    if value.startswith("```") and value.endswith("```"):
        value = value.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        generated = json.loads(value)
        if not isinstance(generated, dict):
            raise ValueError()
        result = deepcopy(work.draft_json or {})
        if target_type == "WORK":
            result = generated
        else:
            episodes = result.setdefault("episodes", [])
            episode = next((e for e in episodes if e["number"] == path["episode"]), None)
            if target_type == "EPISODE":
                if generated.get("number") != path["episode"] or not generated.get("scenes"):
                    raise ValueError()
                if episode is not None:
                    episodes[episodes.index(episode)] = generated
                else:
                    episodes.append(generated)
                    episodes.sort(key=lambda e: e["number"])
            else:
                scenes = episode["scenes"]
                scene = next((s for s in scenes if s["number"] == path["scene"]), None)
                if target_type == "SCENE":
                    if generated.get("number") != path["scene"] or not generated.get("fragments"):
                        raise ValueError()
                    if scene is not None:
                        scenes[scenes.index(scene)] = generated
                    else:
                        scenes.append(generated)
                        scenes.sort(key=lambda s: s["number"])
                else:
                    if generated.get("id") != path["fragment"] or not generated.get("blocks"):
                        raise ValueError()
                    fragments = scene["fragments"]
                    index = next(i for i, f in enumerate(fragments) if f["id"] == path["fragment"])
                    fragments[index] = generated
        validate_script(result, allowed_blocks(work))
        if not result.get("episodes") or any(not e["scenes"] or any(not scene["fragments"] or any(not fragment["blocks"] for fragment in scene["fragments"]) for scene in e["scenes"]) for e in result["episodes"]):
            raise ValueError()
        if any(e["number"] > (work.settings_json or {}).get("episode_count", 200) for e in result["episodes"]):
            raise ValueError()
        return result
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError):
        raise ModelAdapterError("SCRIPT_STRUCTURE_INVALID", "model output did not match the requested script structure") from None
