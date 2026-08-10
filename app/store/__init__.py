"""长期记忆的持久化层（记忆与追问文档 §6）。

记忆是**长期**数据，不能跟 checkpointer 一样放内存。第一步用 SQLite 单文件：
零运维、够用到几千个 profile，`--workers 1` 的现状下也没有并发问题。
上多副本时和 checkpointer 一起迁 Postgres。
"""

from app.store.memory import MemoryStore, get_store, reset_store

__all__ = ["MemoryStore", "get_store", "reset_store"]
