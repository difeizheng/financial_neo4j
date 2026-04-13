"""
chat_store.py

SQLite-backed persistent chat history for Step 4 conversations.
DB file: tasks/chat_history.db (shared across all tasks)

Tables:
  conversations(id TEXT PK, task_id TEXT, title TEXT, created_at TEXT)
  messages(id INTEGER PK, conversation_id TEXT, role TEXT, content TEXT,
           cypher TEXT, results_json TEXT, created_at TEXT)
"""

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional


class ChatStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id         TEXT PRIMARY KEY,
                    task_id    TEXT NOT NULL,
                    title      TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conv_task ON conversations(task_id);

                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    cypher          TEXT,
                    results_json    TEXT,
                    created_at      TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                );
                CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
            """)

    # ── Conversations ──────────────────────────────────────────────────────────

    def new_conversation(self, task_id: str, title: str = "新对话") -> str:
        """Create a new conversation and return its id."""
        conv_id = str(uuid.uuid4())
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversations(id, task_id, title, created_at) VALUES (?,?,?,?)",
                (conv_id, task_id, title, now),
            )
        return conv_id

    def update_title(self, conv_id: str, title: str):
        with self._connect() as conn:
            conn.execute("UPDATE conversations SET title=? WHERE id=?", (title, conv_id))

    def get_conversations(self, task_id: str) -> list[dict]:
        """Return all conversations for a task, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, created_at FROM conversations "
                "WHERE task_id=? ORDER BY created_at DESC",
                (task_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id: str):
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conv_id,))
            conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))

    # ── Messages ───────────────────────────────────────────────────────────────

    def add_message(
        self,
        conv_id: str,
        role: str,
        content: str,
        cypher: Optional[str] = None,
        results: Optional[list] = None,
    ) -> int:
        """Append a message and return its row id."""
        now = datetime.now().isoformat(timespec="seconds")
        results_json = json.dumps(results, ensure_ascii=False) if results is not None else None
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages(conversation_id, role, content, cypher, results_json, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (conv_id, role, content, cypher, results_json, now),
            )
        return cur.lastrowid

    def get_messages(self, conv_id: str) -> list[dict]:
        """Return all messages for a conversation in order."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, cypher, results_json FROM messages "
                "WHERE conversation_id=? ORDER BY id",
                (conv_id,),
            ).fetchall()
        result = []
        for r in rows:
            msg = {"role": r["role"], "content": r["content"]}
            if r["cypher"]:
                msg["cypher"] = r["cypher"]
            if r["results_json"]:
                try:
                    msg["results"] = json.loads(r["results_json"])
                except Exception:
                    pass
            result.append(msg)
        return result

    def message_count(self, conv_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE conversation_id=?", (conv_id,)
            ).fetchone()
        return row["cnt"] if row else 0
