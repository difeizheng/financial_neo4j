"""
store.py — 试算 SQLite 存储层

DB 文件: tasks/trials.db（与 chat_history.db 同目录）

Tables:
  trials(id, task_id, name, note, status, created_at, completed_at, error_msg)
  change_logs(id, trial_id, indicator_id, indicator_name, old_value, new_value, timestamp, is_deleted)
  recalc_results(trial_id, indicator_id, indicator_name, values_json)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


class TrialStore:
    """封装试算相关的 SQLite 操作。线程安全：每次操作新建连接。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # 允许并发读写
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trials (
                    id           TEXT PRIMARY KEY,
                    task_id      TEXT NOT NULL,
                    name         TEXT NOT NULL,
                    note         TEXT,
                    status       TEXT NOT NULL DEFAULT 'pending',
                    created_at   TEXT NOT NULL,
                    completed_at TEXT,
                    error_msg    TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_trials_task ON trials(task_id);

                CREATE TABLE IF NOT EXISTS change_logs (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    trial_id       TEXT NOT NULL,
                    indicator_id   TEXT NOT NULL,
                    indicator_name TEXT NOT NULL,
                    old_value      REAL,
                    new_value      REAL,
                    timestamp      TEXT NOT NULL,
                    is_deleted     INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (trial_id) REFERENCES trials(id)
                );
                CREATE INDEX IF NOT EXISTS idx_logs_trial ON change_logs(trial_id);

                CREATE TABLE IF NOT EXISTS recalc_results (
                    trial_id       TEXT NOT NULL,
                    indicator_id   TEXT NOT NULL,
                    indicator_name TEXT NOT NULL,
                    values_json    TEXT NOT NULL,
                    PRIMARY KEY (trial_id, indicator_id),
                    FOREIGN KEY (trial_id) REFERENCES trials(id)
                );
                CREATE INDEX IF NOT EXISTS idx_results_trial ON recalc_results(trial_id);
            """)

    # ── Trials ─────────────────────────────────────────────────────────────────

    def create_trial(self, task_id: str, name: str, note: str = "") -> str:
        """创建新试算，返回 trial_id。"""
        trial_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO trials(id, task_id, name, note, status, created_at) VALUES (?,?,?,?,?,?)",
                (trial_id, task_id, name, note or None, "pending", now),
            )
        return trial_id

    def update_trial_status(
        self,
        trial_id: str,
        status: str,
        error_msg: Optional[str] = None,
    ):
        """更新试算状态（pending/running/done/error）。"""
        now = datetime.now().isoformat(timespec="seconds")
        completed_at = now if status in ("done", "error") else None
        with self._connect() as conn:
            conn.execute(
                "UPDATE trials SET status=?, completed_at=?, error_msg=? WHERE id=?",
                (status, completed_at, error_msg, trial_id),
            )

    def update_trial_note(self, trial_id: str, note: str):
        """更新试算备注。"""
        with self._connect() as conn:
            conn.execute("UPDATE trials SET note=? WHERE id=?", (note, trial_id))

    def get_trials(self, task_id: str) -> list[dict]:
        """获取任务的所有试算列表，按创建时间倒序。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, name, note, status, created_at, completed_at, error_msg "
                "FROM trials WHERE task_id=? ORDER BY created_at DESC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trial(self, trial_id: str) -> Optional[dict]:
        """获取单个试算详情。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, task_id, name, note, status, created_at, completed_at, error_msg "
                "FROM trials WHERE id=?",
                (trial_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_trial(self, trial_id: str):
        """删除试算及其所有关联数据。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM recalc_results WHERE trial_id=?", (trial_id,))
            conn.execute("DELETE FROM change_logs WHERE trial_id=?", (trial_id,))
            conn.execute("DELETE FROM trials WHERE id=?", (trial_id,))

    # ── Change Logs ────────────────────────────────────────────────────────────

    def add_change_log(
        self,
        trial_id: str,
        indicator_id: str,
        indicator_name: str,
        old_value: Optional[float],
        new_value: Optional[float],
    ) -> int:
        """添加修改日志记录，返回记录 ID。"""
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO change_logs(trial_id, indicator_id, indicator_name, "
                "old_value, new_value, timestamp) VALUES (?,?,?,?,?,?)",
                (trial_id, indicator_id, indicator_name, old_value, new_value, now),
            )
        return cur.lastrowid

    def get_change_logs(
        self, trial_id: str, include_deleted: bool = False
    ) -> list[dict]:
        """获取试算的修改日志，默认只返回未删除的记录。"""
        sql = (
            "SELECT id, indicator_id, indicator_name, old_value, new_value, timestamp, is_deleted "
            "FROM change_logs WHERE trial_id=?"
        )
        params = [trial_id]
        if not include_deleted:
            sql += " AND is_deleted=0"
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def mark_log_deleted(self, log_id: int):
        """软删除修改日志（is_deleted=1）。"""
        with self._connect() as conn:
            conn.execute("UPDATE change_logs SET is_deleted=1 WHERE id=?", (log_id,))

    def upsert_change_log(
        self,
        trial_id: str,
        indicator_id: str,
        indicator_name: str,
        old_value: Optional[float],
        new_value: Optional[float],
    ) -> int:
        """如果该指标已有未删除的日志则更新，否则新增。返回记录 ID。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM change_logs WHERE trial_id=? AND indicator_id=? AND is_deleted=0",
                (trial_id, indicator_id),
            ).fetchone()
            if row:
                now = datetime.now().isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE change_logs SET new_value=?, timestamp=? WHERE id=?",
                    (new_value, now, row["id"]),
                )
                return row["id"]
            else:
                return self.add_change_log(
                    trial_id, indicator_id, indicator_name, old_value, new_value
                )

    # ── Recalc Results ─────────────────────────────────────────────────────────

    def save_recalc_results(
        self, trial_id: str, results: dict[str, tuple[str, list]]
    ):
        """
        批量保存重算结果。

        results: {indicator_id: (indicator_name, [val_y1, ..., val_y48])}
        """
        rows = [
            (trial_id, ind_id, name, json.dumps(vals, ensure_ascii=False))
            for ind_id, (name, vals) in results.items()
        ]
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO recalc_results(trial_id, indicator_id, indicator_name, values_json) "
                "VALUES (?,?,?,?)",
                rows,
            )

    def get_recalc_results(self, trial_id: str) -> dict[str, dict]:
        """
        获取试算的重算结果。

        Returns: {indicator_id: {name, values: [48 floats]}}
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT indicator_id, indicator_name, values_json FROM recalc_results WHERE trial_id=?",
                (trial_id,),
            ).fetchall()
        out = {}
        for r in rows:
            try:
                vals = json.loads(r["values_json"])
            except Exception:
                vals = []
            out[r["indicator_id"]] = {"name": r["indicator_name"], "values": vals}
        return out

    def get_result_count(self, trial_id: str) -> int:
        """获取试算结果的指标数量。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM recalc_results WHERE trial_id=?",
                (trial_id,),
            ).fetchone()
        return row["cnt"] if row else 0
