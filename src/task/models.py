"""Task data model for multi-task financial report analysis."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


STEP_STATUSES = ("pending", "running", "done", "error")


@dataclass
class StepInfo:
    status: str = "pending"   # pending | running | done | error
    error: Optional[str] = None
    progress_msg: str = ""
    progress_pct: float = 0.0


@dataclass
class TaskMeta:
    task_id: str
    name: str
    created_at: str
    updated_at: str
    excel_filename: str

    step1: StepInfo = field(default_factory=StepInfo)
    step2: StepInfo = field(default_factory=StepInfo)
    step3: StepInfo = field(default_factory=StepInfo)

    # Step 2 stats (filled after completion)
    indicator_count: int = 0
    edge_count: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TaskMeta":
        d = dict(d)
        for key in ("step1", "step2", "step3"):
            if isinstance(d.get(key), dict):
                d[key] = StepInfo(**d[key])
        return cls(**d)

    def save(self, task_dir: Path) -> None:
        """Write meta.json via a tmp file for crash safety.

        On Windows, MoveFileExW (used by Path.replace) can raise PermissionError
        if the target is momentarily open by the UI thread. Retry a few times.
        """
        import time as _time
        content = json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
        target = task_dir / "meta.json"
        tmp = task_dir / "meta.json.tmp"
        tmp.write_text(content, encoding="utf-8")
        for attempt in range(6):
            try:
                tmp.replace(target)
                return
            except PermissionError:
                if attempt < 5:
                    _time.sleep(0.05)
                else:
                    # Last resort: write directly (loses atomicity but doesn't crash)
                    target.write_text(content, encoding="utf-8")
                    try:
                        tmp.unlink(missing_ok=True)
                    except OSError:
                        pass

    @classmethod
    def load(cls, task_dir: Path) -> "TaskMeta":
        data = json.loads((task_dir / "meta.json").read_text(encoding="utf-8"))
        return cls.from_dict(data)

    def touch(self) -> None:
        self.updated_at = datetime.now().isoformat()
