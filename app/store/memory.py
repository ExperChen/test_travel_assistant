"""记忆存储：SQLite 两张表。

不做 ORM——两张表、几个查询，直接 SQL 更清楚（文档 §6）。

**所有写入都是 best-effort**：记忆坏掉不能让行程规划失败。读失败退化成"没有
记忆"，写失败只记日志。这条纪律贯穿本模块的每个公开方法。

sqlite3 是同步库，全部调用包在 `asyncio.to_thread` 里——查询都是毫秒级的
单行读写，不值得为它引入 aiosqlite 依赖，但也不能直接阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.config import settings
from app.core.logging import get_logger
from app.models.memory import (
    MemorySnapshot,
    Preference,
    Profile,
    TripHistory,
)

log = get_logger(__name__)

__all__ = ["MemoryStore", "get_store", "reset_store"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    profile_id  TEXT PRIMARY KEY,
    updated_at  TEXT NOT NULL,
    preferences TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trip_history (
    trip_id     TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL,
    city        TEXT NOT NULL,
    adcode      TEXT NOT NULL DEFAULT '',
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    attractions TEXT NOT NULL DEFAULT '[]',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_history_profile
    ON trip_history (profile_id, start_date DESC);
"""


class MemoryStore:
    """L2 偏好 + L3 履历。

    `path=":memory:"` 用于测试；生产走 `MEMORY_DB_PATH`。
    """

    def __init__(self, path: str | Path | None = None):
        self.path = str(path or settings.memory_db_path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn

        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if self.path != ":memory:":
            # WAL 让读不阻塞写。:memory: 不支持，设了会报错。
            conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn
        return conn

    async def _run(self, fn, *args) -> Any:
        """在线程里跑同步 SQL。串行化：sqlite 连接本身不是并发安全的。"""
        async with self._lock:
            return await asyncio.to_thread(fn, *args)

    async def aclose(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    # ---------------------------------------------------------------- L2

    async def load_profile(self, profile_id: str) -> Profile | None:
        """读偏好。读失败返回 None——等同于"这个人还没有记忆"。"""
        if not profile_id:
            return None
        try:
            row = await self._run(self._select_profile, profile_id)
        except Exception:  # noqa: BLE001 —— 记忆不可用不能拖垮规划
            log.exception("读取 profile 失败", extra={"profile_id": profile_id})
            return None
        if row is None:
            return None
        try:
            return Profile(
                profile_id=row["profile_id"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
                preferences={
                    key: Preference.model_validate(value)
                    for key, value in json.loads(row["preferences"]).items()
                },
            )
        except Exception:  # noqa: BLE001 —— 旧格式/脏数据，当作没有记忆
            log.warning("profile 反序列化失败，忽略", extra={"profile_id": profile_id})
            return None

    def _select_profile(self, profile_id: str) -> sqlite3.Row | None:
        cur = self._connect().execute(
            "SELECT profile_id, updated_at, preferences FROM profiles WHERE profile_id = ?",
            (profile_id,),
        )
        return cur.fetchone()

    async def save_profile(self, profile: Profile) -> bool:
        """写偏好。返回是否成功——失败只记日志，绝不抛。"""
        try:
            payload = json.dumps(
                {k: v.model_dump(mode="json") for k, v in profile.preferences.items()},
                ensure_ascii=False,
            )
            await self._run(
                self._upsert_profile,
                profile.profile_id,
                profile.updated_at.isoformat(),
                payload,
            )
            return True
        except Exception:  # noqa: BLE001
            log.exception("写入 profile 失败", extra={"profile_id": profile.profile_id})
            return False

    def _upsert_profile(self, profile_id: str, updated_at: str, payload: str) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO profiles (profile_id, updated_at, preferences) VALUES (?, ?, ?) "
            "ON CONFLICT(profile_id) DO UPDATE SET updated_at = excluded.updated_at, "
            "preferences = excluded.preferences",
            (profile_id, updated_at, payload),
        )
        conn.commit()

    async def delete_profile(self, profile_id: str) -> bool:
        """清空这个人的全部记忆（偏好 + 履历）。

        **这不是可选项**（文档 §6）——用户有权删掉系统记住的一切。
        """
        try:
            await self._run(self._delete_profile, profile_id)
            return True
        except Exception:  # noqa: BLE001
            log.exception("删除 profile 失败", extra={"profile_id": profile_id})
            return False

    def _delete_profile(self, profile_id: str) -> None:
        conn = self._connect()
        conn.execute("DELETE FROM profiles WHERE profile_id = ?", (profile_id,))
        conn.execute("DELETE FROM trip_history WHERE profile_id = ?", (profile_id,))
        conn.commit()

    async def patch_preference(
        self, profile_id: str, key: str, value: Any, *, on: date | None = None
    ) -> Profile | None:
        """用户手工纠正一条偏好。

        直接置为**高置信度**：这是用户明说的，比采样三次更可信。
        换了城市不该等三次采样才生效（文档 §6）。
        """
        profile = await self.load_profile(profile_id) or Profile(profile_id=profile_id)
        preferences = dict(profile.preferences)
        preferences[key] = Preference(
            value=value, confidence=1.0, samples=1, last_seen=on or date.today()
        )
        updated = profile.model_copy(
            update={"preferences": preferences, "updated_at": datetime.now(UTC)}
        )
        return updated if await self.save_profile(updated) else None

    async def forget_preference(self, profile_id: str, key: str) -> Profile | None:
        profile = await self.load_profile(profile_id)
        if profile is None or key not in profile.preferences:
            return profile
        preferences = {k: v for k, v in profile.preferences.items() if k != key}
        updated = profile.model_copy(
            update={"preferences": preferences, "updated_at": datetime.now(UTC)}
        )
        return updated if await self.save_profile(updated) else None

    # ---------------------------------------------------------------- L3

    async def record_trip(self, profile_id: str, history: TripHistory) -> bool:
        if not profile_id:
            return False
        try:
            await self._run(
                self._insert_history,
                history.trip_id,
                profile_id,
                history.city,
                history.adcode,
                history.start_date.isoformat(),
                history.end_date.isoformat(),
                json.dumps(
                    [a.model_dump(mode="json") for a in history.attractions],
                    ensure_ascii=False,
                ),
                history.created_at.isoformat(),
            )
            return True
        except Exception:  # noqa: BLE001
            log.exception("写入行程履历失败", extra={"trip_id": history.trip_id})
            return False

    def _insert_history(self, *values) -> None:
        conn = self._connect()
        conn.execute(
            "INSERT INTO trip_history "
            "(trip_id, profile_id, city, adcode, start_date, end_date, attractions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trip_id) DO NOTHING",
            values,
        )
        conn.commit()

    async def history(self, profile_id: str, *, limit: int = 50) -> list[TripHistory]:
        if not profile_id:
            return []
        try:
            rows = await self._run(self._select_history, profile_id, limit)
        except Exception:  # noqa: BLE001
            log.exception("读取行程履历失败", extra={"profile_id": profile_id})
            return []

        out: list[TripHistory] = []
        for row in rows:
            try:
                out.append(
                    TripHistory(
                        trip_id=row["trip_id"],
                        city=row["city"],
                        adcode=row["adcode"],
                        start_date=date.fromisoformat(row["start_date"]),
                        end_date=date.fromisoformat(row["end_date"]),
                        attractions=json.loads(row["attractions"]),
                        created_at=datetime.fromisoformat(row["created_at"]),
                    )
                )
            except Exception:  # noqa: BLE001 —— 单条脏数据不该毁掉整个列表
                log.warning("跳过无法解析的履历", extra={"trip_id": row["trip_id"]})
        return out

    def _select_history(self, profile_id: str, limit: int) -> list[sqlite3.Row]:
        cur = self._connect().execute(
            "SELECT trip_id, city, adcode, start_date, end_date, attractions, created_at "
            "FROM trip_history WHERE profile_id = ? ORDER BY start_date DESC LIMIT ?",
            (profile_id, limit),
        )
        return cur.fetchall()

    # ---------------------------------------------------------------- 组合

    async def snapshot(self, profile_id: str, *, today: date | None = None) -> MemorySnapshot:
        """一次取全：L2 偏好 + L3 去过的景点/城市。

        规划开始时调一次，之后各处从快照里读，不再回库。
        """
        if not profile_id:
            return MemorySnapshot()

        today = today or date.today()
        profile = await self.load_profile(profile_id)
        if profile is not None:
            profile = profile.advance_children_ages(today)

        visited: dict[str, date] = {}
        cities: dict[str, date] = {}
        for trip in await self.history(profile_id):
            # 同一景点去过多次，按**最近**那次算
            if trip.city not in cities or cities[trip.city] < trip.end_date:
                cities[trip.city] = trip.end_date
            for a in trip.attractions:
                if a.poi_id and (a.poi_id not in visited or visited[a.poi_id] < trip.end_date):
                    visited[a.poi_id] = trip.end_date

        return MemorySnapshot(profile=profile, visited=visited, visited_cities=cities)


# ---------------------------------------------------------------- 单例
# 和 tools.registry 的客户端一样用可替换的模块级单例：测试要能注入 :memory: 库，
# 否则每个用例都会往开发机上写真文件。
_store: MemoryStore | None = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store


def reset_store(store: MemoryStore | None = None) -> None:
    """测试专用：替换或丢弃全局 store。"""
    global _store
    _store = store
