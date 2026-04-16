"""
manager.py — 试算业务逻辑层

负责：
- 从当前修改创建试算
- 后台线程执行 Excel 重算
- 加载/恢复试算
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.trial.store import TrialStore

logger = logging.getLogger(__name__)


def _make_trial_name() -> str:
    """生成试算名称，格式：试算_YYYYMMDD_HHMMSS"""
    return datetime.now().strftime("试算_%Y%m%d_%H%M%S")


class TrialManager:
    """试算管理器：创建、运行、加载、删除试算。"""

    # 跨会话共享的后台线程字典 {trial_id: threading.Thread}
    _threads: dict[str, threading.Thread] = {}
    _lock = threading.Lock()

    def __init__(self, store: TrialStore, task_manager):
        self.store = store
        self.task_manager = task_manager

    # ── 创建试算 ───────────────────────────────────────────────────────────────

    def create_draft_trial(self, task_id: str, note: str = "") -> str:
        """
        创建一个草稿试算（status=pending），用于记录修改日志。
        每个任务同时只有一个 pending 试算。
        """
        # 检查是否已有 pending 试算
        trials = self.store.get_trials(task_id)
        for t in trials:
            if t["status"] == "pending":
                return t["id"]
        name = _make_trial_name()
        return self.store.create_trial(task_id, name, note)

    def get_or_create_draft(self, task_id: str) -> str:
        """获取或创建当前任务的草稿试算 ID。"""
        trials = self.store.get_trials(task_id)
        for t in trials:
            if t["status"] == "pending":
                return t["id"]
        return self.store.create_trial(task_id, _make_trial_name())

    # ── 后台重算 ───────────────────────────────────────────────────────────────

    def start_recalculation(
        self,
        trial_id: str,
        task_id: str,
        changes: dict[str, float],
        progress_callback=None,
    ) -> threading.Thread:
        """
        启动后台重算线程。

        changes: {indicator_id: new_value}
        progress_callback: callable(msg: str) — 进度消息回调
        """
        t = threading.Thread(
            target=self._recalc_worker,
            args=(trial_id, task_id, changes, progress_callback),
            daemon=True,
        )
        with self._lock:
            self._threads[trial_id] = t
        t.start()
        return t

    def _recalc_worker(
        self,
        trial_id: str,
        task_id: str,
        changes: dict[str, float],
        progress_callback=None,
    ):
        """后台线程：执行重算并保存结果到 SQLite + Neo4j。"""

        def _progress(msg: str):
            logger.info(f"[Trial {trial_id[:8]}] {msg}")
            if progress_callback:
                try:
                    progress_callback(msg)
                except Exception:
                    pass

        try:
            self.store.update_trial_status(trial_id, "running")
            _progress("开始重算...")

            # 加载任务数据
            meta = self.task_manager.get_task(task_id)
            if not meta:
                raise ValueError(f"任务 {task_id} 不存在")

            task_dir = self.task_manager.get_task_dir(task_id)
            excel_path = task_dir / "uploaded.xlsx"
            indicators_path = task_dir / "indicators.json"
            config_path = task_dir / "config.json"

            with open(indicators_path, encoding="utf-8") as f:
                indicators = json.load(f)
            with open(config_path, encoding="utf-8") as f:
                task_config = json.load(f)

            sheet_configs = task_config.get("sheet_configs", {})

            _progress(f"加载 Excel 模型（{len(indicators)} 个指标）...")

            # 执行重算（直接执行，不嵌套线程）
            from src.graph.recalculator import ParameterRecalculator

            recalc = ParameterRecalculator(
                task_id=task_id,
                excel_path=str(excel_path),
                indicators=indicators,
                sheet_configs=sheet_configs,
            )

            _progress("开始 Excel 重算...")
            new_values = recalc.recalculate(changes=changes)
            _progress(f"重算完成，共 {len(new_values)} 个指标")

            # 构建指标名称映射
            ind_name_map = {ind["id"]: ind.get("name", ind["id"]) for ind in indicators}

            # 保存结果到 SQLite（只保存有值的指标）
            _progress("保存重算结果到数据库...")
            results_for_store = {
                ind_id: (ind_name_map.get(ind_id, ind_id), vals)
                for ind_id, vals in new_values.items()
                if vals
            }
            self.store.save_recalc_results(trial_id, results_for_store)

            # 更新 Neo4j
            _progress("更新 Neo4j 图数据库...")
            from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
            from src.graph.loader import GraphLoader

            with GraphLoader(
                uri=NEO4J_URI,
                user=NEO4J_USER,
                password=NEO4J_PASSWORD,
                task_id=task_id,
            ) as loader:
                loader.update_indicator_values(new_values)

            # 更新 param_overrides.json（保持文件系统同步）
            overrides_path = task_dir / "param_overrides.json"
            _progress("更新参数覆盖文件...")
            try:
                existing = {}
                if overrides_path.exists():
                    with open(overrides_path, encoding="utf-8") as f:
                        existing = json.load(f)
                for ind_id, new_val in changes.items():
                    existing[ind_id] = [new_val]
                tmp = overrides_path.with_suffix(".tmp")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(existing, f, ensure_ascii=False, indent=2)
                tmp.replace(overrides_path)
            except Exception as e:
                logger.warning(f"更新 param_overrides.json 失败: {e}")

            self.store.update_trial_status(trial_id, "done")
            _progress("✅ 重算完成")

        except Exception as e:
            logger.exception(f"试算 {trial_id} 重算失败")
            self.store.update_trial_status(trial_id, "error", str(e))
            if progress_callback:
                try:
                    progress_callback(f"❌ 重算失败: {e}")
                except Exception:
                    pass
        finally:
            with self._lock:
                self._threads.pop(trial_id, None)

    def is_running(self, trial_id: str) -> bool:
        """检查试算是否正在运行。"""
        with self._lock:
            t = self._threads.get(trial_id)
        return t is not None and t.is_alive()

    # ── 试算管理 ───────────────────────────────────────────────────────────────

    def restore_original_values(self, task_id: str):
        """
        恢复原始值：
        1. 从 param_snapshot.json 读取原始值
        2. 更新 Neo4j
        3. 删除 param_overrides.json
        """
        task_dir = self.task_manager.get_task_dir(task_id)
        snapshot_path = task_dir / "param_snapshot.json"
        overrides_path = task_dir / "param_overrides.json"

        if not snapshot_path.exists():
            raise FileNotFoundError("未找到原始值快照，无法恢复")

        with open(snapshot_path, encoding="utf-8") as f:
            snapshot = json.load(f)

        # snapshot 格式: {ind_id: [val_y1, ..., val_y48]}
        from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
        from src.graph.loader import GraphLoader

        with GraphLoader(
            uri=NEO4J_URI,
            user=NEO4J_USER,
            password=NEO4J_PASSWORD,
            task_id=task_id,
        ) as loader:
            loader.update_indicator_values(snapshot)

        if overrides_path.exists():
            overrides_path.unlink()

    def get_trial_changes_as_dict(self, trial_id: str) -> dict[str, float]:
        """从修改日志中提取 {indicator_id: new_value} 字典。"""
        logs = self.store.get_change_logs(trial_id, include_deleted=False)
        return {log["indicator_id"]: log["new_value"] for log in logs}
