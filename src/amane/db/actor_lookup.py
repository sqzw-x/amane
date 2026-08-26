"""演员名字解析与查找 - 展示名 + ActorAlias 形态.

名字→演员 (``resolve_actor_by_name``) 是唯一解析入口, 三态判定:

1. 展示名精确命中 (全局唯一, 优先).
2. 别名唯一命中 (某演员独有的别名行).
3. 歧义 (多演员共享该别名) / 无命中: 以该名字本身 get-or-create 展示名实体 —
   确定性且与旧行为一致 (不该偷偷归给任一候选), 用户可后续 merge 收拢.

block 判定 (FacetRule actor block) 不在此层, 由调用方在解析前后各查一次.
"""

from __future__ import annotations

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from amane.db.models import Actor, ActorAlias


async def list_actor_aliases(session: AsyncSession, actor_id: int) -> list[str]:
    """演员别名行 (按 position 保序); 不含展示名."""
    stmt = (
        select(ActorAlias.name)
        .where(ActorAlias.actor_id == actor_id)
        .order_by(col(ActorAlias.position), col(ActorAlias.id))
    )
    return list((await session.exec(stmt)).all())


async def build_actor_lookup_names(session: AsyncSession, actor: Actor) -> list[str]:
    """有序候选名: 展示名 → 别名行 (position 保序)."""
    return [actor.name, *await list_actor_aliases(session, actor.id or 0)]


async def resolve_actor_by_name(session: AsyncSession, name: str) -> Actor | None:
    """名字 → Actor 实体 (get-or-create); 空名返回 None.

    展示名命中优先; 否则别名表唯命中; 歧义或无命中时以该名字新建展示名实体.
    """
    if not name:
        return None
    existing = (await session.exec(select(Actor).where(Actor.name == name))).first()
    if existing is not None:
        return existing
    rows = (await session.exec(select(ActorAlias.actor_id).where(ActorAlias.name == name))).all()
    ids = {int(i) for i in rows}
    if len(ids) == 1:
        actor = await session.get(Actor, next(iter(ids)))
        if actor is not None:
            return actor
    actor = Actor(name=name)
    session.add(actor)
    await session.flush()
    return actor


async def lookup_actors_by_name(session: AsyncSession, name: str) -> list[Actor]:
    """只读名字→演员候选: 展示名命中 + 别名行命中 (去重); **不创建实体**.

    顺序为展示名命中在前, 其余按 id 升序; 多命中即共享别名 (歧义), 由调用方决定处置.
    """
    if not name:
        return []
    found: list[Actor] = []
    display = (await session.exec(select(Actor).where(Actor.name == name))).first()
    if display is not None:
        found.append(display)
    rows = (
        await session.exec(
            select(Actor)
            .join(ActorAlias, col(ActorAlias.actor_id) == col(Actor.id))
            .where(col(ActorAlias.name) == name)
            .order_by(col(Actor.id))
        )
    ).all()
    seen = {a.id for a in found}
    for actor in rows:
        if actor.id not in seen:
            found.append(actor)
            seen.add(actor.id)
    return found
