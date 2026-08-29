from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Unpack

from sqlalchemy import delete as sqla_delete
from sqlalchemy import func, or_
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.functions import count
from sqlmodel import col, select

from ...parsing import ContentType
from ..models import Feed, FeedItem, FeedItemState, Metadata
from ..repo_types import FeedUpdates
from .base import RepositoryMixinBase


def _unique_item_ids(item_ids: Sequence[int]) -> list[int]:
    return list(dict.fromkeys(item_ids))


class FeedsRepoMixin(RepositoryMixinBase):
    async def create_feed(
        self,
        name: str,
        url: str,
        *,
        enabled: bool = True,
        auto_enqueue: bool = True,
        interval_seconds: int = 3600,
        number_pattern: str | None = None,
        content_type: ContentType | None = None,
        use_cache: list[str] | None = None,
        group: str = "",
    ) -> Feed:
        async with self._session() as session:
            feed = Feed(
                name=name,
                url=url,
                group=group,
                enabled=enabled,
                auto_enqueue=auto_enqueue,
                interval_seconds=interval_seconds,
                number_pattern=number_pattern,
                content_type=content_type,
                use_cache=list(use_cache) if use_cache is not None else ["metadata", "trans"],
            )
            session.add(feed)
            await session.commit()
            await session.refresh(feed)
            return feed

    async def list_feeds(self) -> list[Feed]:
        async with self._session() as session:
            result = await session.exec(select(Feed).order_by(col(Feed.group), col(Feed.name), col(Feed.id)))
            return list(result.all())

    async def list_due_feeds(self, now: datetime) -> list[Feed]:
        async with self._session() as session:
            stmt = (
                select(Feed)
                .where(Feed.enabled == True)  # noqa: E712
                .where((col(Feed.next_fetch_at).is_(None)) | (col(Feed.next_fetch_at) <= now))
                .order_by(col(Feed.id))
            )
            result = await session.exec(stmt)
            return list(result.all())

    async def get_feed(self, feed_id: int) -> Feed | None:
        async with self._session() as session:
            return await session.get(Feed, feed_id)

    async def get_feed_by_url(self, url: str) -> Feed | None:
        async with self._session() as session:
            result = await session.exec(select(Feed).where(Feed.url == url))
            return result.first()

    async def update_feed(self, feed_id: int, **updates: Unpack[FeedUpdates]) -> Feed | None:
        async with self._session() as session:
            feed = await session.get(Feed, feed_id)
            if feed is None:
                return None
            if "name" in updates:
                feed.name = updates["name"]
            if "url" in updates:
                feed.url = updates["url"]
            if "group" in updates:
                feed.group = updates["group"]
            if "enabled" in updates:
                feed.enabled = updates["enabled"]
            if "auto_enqueue" in updates:
                feed.auto_enqueue = updates["auto_enqueue"]
            if "interval_seconds" in updates:
                feed.interval_seconds = updates["interval_seconds"]
            if "number_pattern" in updates:
                feed.number_pattern = updates["number_pattern"]
            if "content_type" in updates:
                feed.content_type = updates["content_type"]
            if "use_cache" in updates:
                feed.use_cache = updates["use_cache"]
            if "etag" in updates:
                feed.etag = updates["etag"]
            if "last_modified" in updates:
                feed.last_modified = updates["last_modified"]
            if "next_fetch_at" in updates:
                feed.next_fetch_at = updates["next_fetch_at"]
            if "last_fetched_at" in updates:
                feed.last_fetched_at = updates["last_fetched_at"]
            if "last_error" in updates:
                feed.last_error = updates["last_error"]
            if "last_enqueued" in updates:
                feed.last_enqueued = updates["last_enqueued"]
            session.add(feed)
            await session.commit()
            await session.refresh(feed)
            return feed

    async def delete_feed(self, feed_id: int) -> bool:
        """删除订阅源并级联删除其 FeedItem."""
        async with self._session() as session:
            feed = await session.get(Feed, feed_id)
            if feed is None:
                return False
            items = list((await session.exec(select(FeedItem).where(FeedItem.feed_id == feed_id))).all())
            for item in items:
                await session.delete(item)
            await session.flush()
            await session.delete(feed)
            await session.commit()
            return True

    async def list_feed_item_keys(self, feed_id: int) -> set[str]:
        async with self._session() as session:
            result = await session.exec(select(FeedItem.item_key).where(FeedItem.feed_id == feed_id))
            return set(result.all())

    async def list_feed_items_by_ids(self, feed_id: int, item_ids: Sequence[int]) -> tuple[list[FeedItem], int]:
        """按 Feed 限定加载 FeedItem, 返回 (匹配行, missing 数)."""
        ids = _unique_item_ids(item_ids)
        if not ids:
            return [], 0

        async with self._session() as session:
            result = await session.exec(
                select(FeedItem).where(col(FeedItem.feed_id) == feed_id, col(FeedItem.id).in_(ids))
            )
            by_id = {item.id: item for item in result.all() if item.id is not None}
            items = [by_id[item_id] for item_id in ids if item_id in by_id]
            return items, len(ids) - len(items)

    async def create_feed_item(
        self,
        feed_id: int,
        item_key: str,
        *,
        title: str | None = None,
        link: str | None = None,
        description: str | None = None,
        number: str | None = None,
        published_at: datetime | None = None,
    ) -> FeedItem:
        async with self._session() as session:
            item = FeedItem(
                feed_id=feed_id,
                item_key=item_key,
                title=title,
                link=link,
                description=description,
                number=number,
                published_at=published_at,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)
            return item

    async def backfill_feed_item_snapshots(
        self,
        feed_id: int,
        *,
        descriptions: dict[str, str],
        published_at: dict[str, datetime],
    ) -> None:
        """只填空: description / published_at 已有值保持首次快照, 不随源更新."""
        keys = set(descriptions) | set(published_at)
        if not keys:
            return
        async with self._session() as session:
            result = await session.exec(
                select(FeedItem).where(col(FeedItem.feed_id) == feed_id, col(FeedItem.item_key).in_(list(keys)))
            )
            changed = False
            for item in result.all():
                updated = False
                if not item.description:
                    body = descriptions.get(item.item_key)
                    if body:
                        item.description = body
                        updated = True
                if item.published_at is None:
                    published = published_at.get(item.item_key)
                    if published is not None:
                        item.published_at = published
                        updated = True
                if updated:
                    session.add(item)
                    changed = True
            if changed:
                await session.commit()

    async def list_feed_items(
        self,
        feed_id: int | None = None,
        offset: int = 0,
        limit: int = 50,
        *,
        search: str | None = None,
        state: FeedItemState | str = FeedItemState.ACTIVE,
        group: str | None = None,
    ) -> tuple[list[tuple[FeedItem, int | None]], int]:
        async with self._session() as session:
            try:
                normalized_state = state if isinstance(state, FeedItemState) else FeedItemState(state)
            except ValueError as exc:
                raise ValueError(f"Unknown feed item state: {state}") from exc

            filters: list[ColumnElement[bool]] = []
            join_feed = False
            if feed_id is not None:
                filters.append(col(FeedItem.feed_id) == feed_id)
            elif group is not None:
                join_feed = True
                if group == "":
                    filters.append(col(Feed.group) == "")
                else:
                    escaped = group.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    filters.append(or_(col(Feed.group) == group, col(Feed.group).like(f"{escaped}/%", escape="\\")))

            if normalized_state is FeedItemState.ACTIVE:
                filters.append(col(FeedItem.ignored_at).is_(None))
            elif normalized_state is FeedItemState.IGNORED:
                filters.append(col(FeedItem.ignored_at).is_not(None))

            normalized_search = search.strip() if search is not None else None
            if normalized_search:
                pattern = f"%{normalized_search}%"
                filters.append(
                    or_(
                        col(FeedItem.number).ilike(pattern),
                        col(FeedItem.title).ilike(pattern),
                        col(FeedItem.description).ilike(pattern),
                        col(FeedItem.link).ilike(pattern),
                        col(FeedItem.item_key).ilike(pattern),
                    )
                )

            total_stmt = select(count()).select_from(FeedItem)
            if join_feed:
                total_stmt = total_stmt.join(Feed, col(FeedItem.feed_id) == col(Feed.id))
            if filters:
                total_stmt = total_stmt.where(*filters)
            total = int((await session.exec(total_stmt)).one())

            # 先取当前页 id 再 JOIN Metadata. OUTER JOIN 与 ORDER BY 写在同一句时,
            # SQLite 会先连接再截页; COLLATE NOCASE 才能走 ix_metadata_number.
            id_stmt = select(col(FeedItem.id))
            if join_feed:
                id_stmt = id_stmt.join(Feed, col(FeedItem.feed_id) == col(Feed.id))
            if filters:
                id_stmt = id_stmt.where(*filters)
            id_stmt = (
                id_stmt.order_by(
                    func.coalesce(col(FeedItem.published_at), col(FeedItem.created_at)).desc(), col(FeedItem.id).desc()
                )
                .offset(offset)
                .limit(limit)
            )
            page_ids = [item_id for item_id in (await session.exec(id_stmt)).all() if item_id is not None]
            if not page_ids:
                return [], total

            page_stmt = (
                select(FeedItem, col(Metadata.id))
                .outerjoin(Metadata, col(FeedItem.number) == col(Metadata.number).collate("NOCASE"))
                .where(col(FeedItem.id).in_(page_ids))
            )
            by_id = {
                item.id: (item, metadata_id)
                for item, metadata_id in (await session.exec(page_stmt)).all()
                if item.id is not None
            }
            return [by_id[item_id] for item_id in page_ids], total

    async def ignore_feed_items(self, feed_id: int, item_ids: list[int]) -> tuple[int, int]:
        """幂等忽略属于指定 Feed 的历史条目."""
        return await self._set_feed_items_ignored(feed_id, item_ids, ignored=True)

    async def unignore_feed_items(self, feed_id: int, item_ids: list[int]) -> tuple[int, int]:
        """幂等恢复属于指定 Feed 的历史条目."""
        return await self._set_feed_items_ignored(feed_id, item_ids, ignored=False)

    async def _set_feed_items_ignored(self, feed_id: int, item_ids: list[int], *, ignored: bool) -> tuple[int, int]:
        ids = _unique_item_ids(item_ids)
        if not ids:
            return 0, 0

        async with self._session() as session:
            result = await session.exec(
                select(FeedItem).where(col(FeedItem.feed_id) == feed_id, col(FeedItem.id).in_(ids))
            )
            items = list(result.all())
            now = datetime.now(UTC) if ignored else None
            for item in items:
                if ignored and item.ignored_at is None:
                    item.ignored_at = now
                    session.add(item)
                elif not ignored and item.ignored_at is not None:
                    item.ignored_at = None
                    session.add(item)
            await session.commit()
            return len(items), len(ids) - len(items)

    async def delete_feed_items(self, feed_id: int, item_ids: list[int]) -> tuple[int, int]:
        """永久删除属于指定 Feed 的历史条目, 不影响已创建任务或元数据."""
        ids = _unique_item_ids(item_ids)
        if not ids:
            return 0, 0

        async with self._session() as session:
            result = await session.exec(
                select(FeedItem.id).where(col(FeedItem.feed_id) == feed_id, col(FeedItem.id).in_(ids))
            )
            existing_ids = [item_id for item_id in result.all() if item_id is not None]
            if existing_ids:
                await session.exec(
                    sqla_delete(FeedItem).where(col(FeedItem.feed_id) == feed_id, col(FeedItem.id).in_(existing_ids))
                )
            await session.commit()
            return len(existing_ids), len(ids) - len(existing_ids)
