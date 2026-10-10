"""独立翻译入口: 只经 LLM 翻译库内既有文本, 不重新刮削.

锁语义不在本层重新实现: 锁集由 ``db`` 侧的公开判定给出, 写入仍走 ``WriteMode.AUTO`` 再过滤一次 —
两次判定之间锁集合可能变化, 后一次是兜底.
译文缓存复用刮削侧同一实例, 但强制跳过读取 (``use_cache=False``): 缓存键不含 model, 用户换模型后
再按按钮不应拿到旧译文; 仍回写缓存, 使随后的重刮零 token.
"""

from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, HTTPException

from ...db.actor_person import locked_fields_of as locked_actor_fields
from ...db.repo_types import WriteMode
from ...db.repos.metadata import locked_fields_of as locked_metadata_fields
from ...enums import ActorField, MetadataField
from ...utils.language import needs_llm_translation
from ..deps import RepoDep, RuntimeDep
from ..models import (
    ActorTranslationOutcome,
    ActorTranslationResponse,
    MetadataTranslationOutcome,
    MetadataTranslationResponse,
    TranslationStatus,
)

if TYPE_CHECKING:
    from ...app.runtime import AppRuntime
    from ...db.repo_types import ActorPersonFields, MetadataFields
    from ...enums import Language, TranslateField
    from ...llm import Translator

logger = structlog.get_logger()

router = APIRouter(prefix="/translation", tags=["translation"])


def _require_translator(runtime: AppRuntime) -> Translator:
    """未启用或缺密钥时 translator 为 None; 该入口没有可执行的动作."""
    translator = runtime.translator
    if translator is None:
        raise HTTPException(status_code=503, detail="未启用 LLM 翻译")
    return translator


async def _translate_text(
    translator: Translator, text: str, target: Language, field: TranslateField
) -> tuple[TranslationStatus, str | None]:
    """翻译单个字段并判定状态; 需要写入时返回译文, 否则返回 ``None``.

    ``needs_llm_translation`` 为假时翻译器返回空结果属正常 (文本已是目标语言), 归 ``UNCHANGED``;
    为真却拿不到结果才算 ``FAILED`` — ``Translator`` 只有 ``str | None`` 一条返回路径, 两态只能在这里分开.
    """
    needed = needs_llm_translation(text, target)
    try:
        translated = await translator.translate(text, target, field, use_cache=False)
    except Exception as e:
        logger.warning("translation failed, keeping original", field=str(field), error=str(e))
        return TranslationStatus.FAILED, None
    if not translated:
        return (TranslationStatus.FAILED if needed else TranslationStatus.UNCHANGED), None
    if translated == text:
        # 逐字相同不写库: 避免 updated_at 与补刮排序无谓变动.
        return TranslationStatus.UNCHANGED, None
    return TranslationStatus.TRANSLATED, translated


@router.post("/metadata/{metadata_id}")
async def translate_metadata(metadata_id: int, repo: RepoDep, runtime: RuntimeDep) -> MetadataTranslationResponse:
    """按 ``llm.translate_fields`` 翻译标题与剧情并写回; 锁定字段跳过."""
    translator = _require_translator(runtime)
    metadata = await repo.get_metadata(metadata_id)
    if metadata is None:
        raise HTTPException(status_code=404, detail="影片不存在")

    llm = runtime.config.hot.llm
    locked = locked_metadata_fields(metadata)
    updates: MetadataFields = {}
    outcomes: list[MetadataTranslationOutcome] = []
    for field in (MetadataField.TITLE, MetadataField.PLOT):
        if field not in llm.translate_fields:
            continue
        text = metadata.title if field is MetadataField.TITLE else metadata.plot
        if not text:
            continue
        if field in locked:
            outcomes.append(MetadataTranslationOutcome(field=field, status=TranslationStatus.LOCKED))
            continue
        target = runtime.config.hot.scraping.field_language.get(field)
        if target is None:
            continue
        status, translated = await _translate_text(translator, text, target, field)
        if translated is not None:
            updates[field.value] = translated
        outcomes.append(MetadataTranslationOutcome(field=field, status=status))

    if updates and await repo.update_metadata(metadata_id, mode=WriteMode.AUTO, **updates) is None:
        raise HTTPException(status_code=404, detail="影片不存在")
    logger.info("metadata translated", metadata_id=metadata_id, outcomes=[str(item.status) for item in outcomes])
    return MetadataTranslationResponse(outcomes=outcomes)


@router.post("/actors/{actor_id}")
async def translate_actor(actor_id: int, repo: RepoDep, runtime: RuntimeDep) -> ActorTranslationResponse:
    """按 ``llm.actor_translate_fields`` 翻译简介与标语并写回; 锁定字段跳过."""
    translator = _require_translator(runtime)
    actor = await repo.get_actor(actor_id)
    if actor is None:
        raise HTTPException(status_code=404, detail="演员不存在")

    llm = runtime.config.hot.llm
    locked = locked_actor_fields(actor)
    updates: ActorPersonFields = {}
    outcomes: list[ActorTranslationOutcome] = []
    for field in (ActorField.OVERVIEW, ActorField.TAGLINE):
        if field not in llm.actor_translate_fields:
            continue
        text = actor.overview if field is ActorField.OVERVIEW else actor.tagline
        if not text:
            continue
        if field in locked:
            outcomes.append(ActorTranslationOutcome(field=field, status=TranslationStatus.LOCKED))
            continue
        status, translated = await _translate_text(translator, text, llm.actor_language, field)
        if translated is not None:
            updates[field.value] = translated
        outcomes.append(ActorTranslationOutcome(field=field, status=status))

    if updates and await repo.update_actor(actor_id, mode=WriteMode.AUTO, **updates) is None:
        raise HTTPException(status_code=404, detail="演员不存在")
    logger.info("actor translated", actor_id=actor_id, outcomes=[str(item.status) for item in outcomes])
    return ActorTranslationResponse(outcomes=outcomes)
