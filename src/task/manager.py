"""TaskManager: CRUD operations for tasks, backed by the filesystem."""
from __future__ import annotations
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .models import TaskMeta, StepInfo


class TaskManager:
    def __init__(self, tasks_dir: Path):
        self.tasks_dir = tasks_dir
        self.tasks_dir.mkdir(parents=True, exist_ok=True)

    def create_task(self, name: str, excel_bytes: bytes, filename: str) -> TaskMeta:
        task_id = uuid.uuid4().hex
        task_dir = self.tasks_dir / task_id
        task_dir.mkdir(parents=True)

        # Save uploaded Excel
        (task_dir / "uploaded.xlsx").write_bytes(excel_bytes)

        now = datetime.now().isoformat()
        meta = TaskMeta(
            task_id=task_id,
            name=name,
            created_at=now,
            updated_at=now,
            excel_filename=filename,
        )
        meta.save(task_dir)
        return meta

    def list_tasks(self) -> List[TaskMeta]:
        tasks = []
        for task_dir in sorted(self.tasks_dir.iterdir()):
            if task_dir.is_dir() and (task_dir / "meta.json").exists():
                try:
                    tasks.append(TaskMeta.load(task_dir))
                except Exception:
                    pass
        # Sort newest first
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks

    def get_task(self, task_id: str) -> Optional[TaskMeta]:
        task_dir = self.tasks_dir / task_id
        meta_path = task_dir / "meta.json"
        if not meta_path.exists():
            return None
        # Retry once in case of a transient write (race between background thread and UI)
        for attempt in range(2):
            try:
                return TaskMeta.load(task_dir)
            except (ValueError, KeyError, TypeError):
                if attempt == 0:
                    import time; time.sleep(0.05)
                else:
                    raise

    def save_task(self, meta: TaskMeta) -> None:
        meta.touch()
        meta.save(self.tasks_dir / meta.task_id)

    def delete_task(self, task_id: str) -> None:
        task_dir = self.tasks_dir / task_id
        if task_dir.exists():
            shutil.rmtree(task_dir)

    def get_task_dir(self, task_id: str) -> Path:
        return self.tasks_dir / task_id

    def get_excel_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "uploaded.xlsx"

    def get_config_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "config.json"

    def get_indicators_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "indicators.json"

    def get_dependencies_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "dependencies.json"

    def get_child_relationships_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "child_relationships.json"

    def get_coverage_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "coverage.json"

    def get_log_path(self, task_id: str, step: int = 0) -> Path:
        if step:
            return self.tasks_dir / task_id / f"step{step}.log"
        return self.tasks_dir / task_id / "pipeline.log"

    def append_log(self, task_id: str, message: str, step: int = 0) -> None:
        log_path = self.get_log_path(task_id, step)
        ts = datetime.now().strftime("%H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {message}\n")

    def read_log(self, task_id: str, step: int = 0) -> str:
        log_path = self.get_log_path(task_id, step)
        if not log_path.exists():
            return ""
        return log_path.read_text(encoding="utf-8")

    def clear_log(self, task_id: str, step: int = 0) -> None:
        log_path = self.get_log_path(task_id, step)
        if log_path.exists():
            log_path.unlink()

    def get_param_overrides_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "param_overrides.json"

    def get_param_snapshot_path(self, task_id: str) -> Path:
        return self.tasks_dir / task_id / "param_snapshot.json"
