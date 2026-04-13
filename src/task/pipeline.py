"""PipelineRunner: executes the 4-step analysis pipeline in background threads."""
from __future__ import annotations
import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .manager import TaskManager
from .models import StepInfo


class _StopRequested(Exception):
    """Raised inside a worker when stop is requested."""


def _fmt_time(seconds: float) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{int(seconds)}秒"
    return f"{int(seconds // 60)}分{int(seconds % 60)}秒"


def _eta_suffix(elapsed: float, progress: float) -> str:
    """Return ' | 已用时 Xs | 预计剩余 Ys' string, or just elapsed if progress too low."""
    elapsed_str = f"已用时 {_fmt_time(elapsed)}"
    if progress > 0.05:
        total_est = elapsed / progress
        remaining = max(0.0, total_est - elapsed)
        return f" | {elapsed_str} | 预计剩余 {_fmt_time(remaining)}"
    return f" | {elapsed_str}"


class PipelineRunner:
    """Runs pipeline steps in background threads. Progress is written to filesystem."""

    def __init__(self, task_manager: TaskManager):
        self.tm = task_manager
        self._threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}

    def is_running(self, task_id: str, step: int) -> bool:
        key = f"{task_id}_step{step}"
        t = self._threads.get(key)
        return t is not None and t.is_alive()

    def stop_step(self, task_id: str, step: int) -> None:
        """Signal a running step to stop (in-session event + cross-session file)."""
        key = f"{task_id}_step{step}"
        ev = self._stop_events.get(key)
        if ev:
            ev.set()
        # Write a stop-signal file so cross-session stops also work
        try:
            self._stop_file(task_id, step).touch()
        except OSError:
            pass

    def _stop_file(self, task_id: str, step: int) -> Path:
        return self.tm.get_task_dir(task_id) / f"step{step}.stop"

    def _make_stop_event(self, task_id: str, step: int) -> threading.Event:
        key = f"{task_id}_step{step}"
        ev = threading.Event()
        self._stop_events[key] = ev
        # Clear any leftover stop file from a previous run
        try:
            self._stop_file(task_id, step).unlink(missing_ok=True)
        except OSError:
            pass
        return ev

    def _make_stop_checker(self, stop_event: threading.Event, task_id: str, step: int):
        """Return a zero-arg callable that raises _StopRequested when stop is requested.
        Checks both the in-session threading.Event and the cross-session stop file."""
        stop_file = self._stop_file(task_id, step)

        def check():
            if stop_event.is_set():
                raise _StopRequested()
            if stop_file.exists():
                try:
                    stop_file.unlink(missing_ok=True)
                except OSError:
                    pass
                raise _StopRequested()

        return check

    # ------------------------------------------------------------------ #
    # Step 1: LLM config generation
    # ------------------------------------------------------------------ #

    def run_step1(self, task_id: str, llm_callable, feedback: Optional[str] = None) -> None:
        key = f"{task_id}_step1"
        if self.is_running(task_id, 1):
            return
        stop_ev = self._make_stop_event(task_id, 1)
        t = threading.Thread(
            target=self._step1_worker,
            args=(task_id, llm_callable, feedback, stop_ev),
            daemon=True,
        )
        self._threads[key] = t
        t.start()

    def _step1_worker(self, task_id: str, llm_callable, feedback: Optional[str], stop_ev: threading.Event) -> None:
        tm = self.tm
        check_stop = self._make_stop_checker(stop_ev, task_id, 1)
        meta = tm.get_task(task_id)
        meta.step1 = StepInfo(status="running", progress_msg="正在分析Excel结构...", progress_pct=0.1)
        tm.save_task(meta)
        tm.clear_log(task_id, step=1)
        tm.append_log(task_id, "Step 1 开始：分析Excel结构", step=1)

        try:
            from src.parser.excel_analyzer import analyze_excel
            from src.parser.config_generator import generate_config

            excel_path = tm.get_excel_path(task_id)
            tm.append_log(task_id, f"读取Excel: {excel_path.name}", step=1)
            check_stop()

            meta.step1.progress_msg = "提取Excel结构元数据..."
            meta.step1.progress_pct = 0.2
            tm.save_task(meta)

            excel_meta = analyze_excel(excel_path)
            sheet_count = len(excel_meta.get("sheets", []))
            tm.append_log(task_id, f"发现 {sheet_count} 个工作表", step=1)
            check_stop()

            meta.step1.progress_msg = "调用LLM生成解析配置..."
            meta.step1.progress_pct = 0.5
            tm.save_task(meta)

            config = generate_config(excel_meta, llm_callable, feedback=feedback)
            tm.append_log(task_id, f"LLM生成配置完成，包含 {len(config.get('sheet_configs', {}))} 个sheet配置", step=1)

            config_path = tm.get_config_path(task_id)
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            tm.append_log(task_id, "配置已保存", step=1)

            meta.step1 = StepInfo(status="done", progress_msg="配置生成完成", progress_pct=1.0)
            tm.save_task(meta)

        except _StopRequested:
            tm.append_log(task_id, "Step 1 已停止", step=1)
            meta.step1 = StepInfo(status="error", error="用户停止", progress_msg="已停止")
            tm.save_task(meta)
        except Exception as e:
            tm.append_log(task_id, f"Step 1 错误: {e}", step=1)
            meta.step1 = StepInfo(status="error", error=str(e), progress_msg=str(e))
            tm.save_task(meta)

    # ------------------------------------------------------------------ #
    # Step 2: Parse Excel → JSON
    # ------------------------------------------------------------------ #

    def run_step2(self, task_id: str) -> None:
        key = f"{task_id}_step2"
        if self.is_running(task_id, 2):
            return
        stop_ev = self._make_stop_event(task_id, 2)
        t = threading.Thread(
            target=self._step2_worker,
            args=(task_id, stop_ev),
            daemon=True,
        )
        self._threads[key] = t
        t.start()

    def _step2_worker(self, task_id: str, stop_ev: threading.Event) -> None:
        tm = self.tm
        check_stop = self._make_stop_checker(stop_ev, task_id, 2)
        meta = tm.get_task(task_id)
        meta.step2 = StepInfo(status="running", progress_msg="开始解析Excel...", progress_pct=0.05)
        tm.save_task(meta)
        tm.clear_log(task_id, step=2)
        tm.append_log(task_id, "Step 2 开始：解析Excel", step=2)
        start_time = time.time()

        def update(msg: str, pct: float) -> None:
            elapsed = time.time() - start_time
            full_msg = msg + _eta_suffix(elapsed, pct)
            meta.step2.progress_msg = full_msg
            meta.step2.progress_pct = min(pct, 0.99)
            tm.save_task(meta)
            tm.append_log(task_id, full_msg, step=2)

        try:
            from src.parser.indicator_registry import extract_indicators
            from src.parser.value_extractor import extract_values
            from src.parser.formula_parser import parse_dependencies

            excel_path = tm.get_excel_path(task_id)
            config_path = tm.get_config_path(task_id)

            config = json.loads(config_path.read_text(encoding="utf-8"))
            sheet_configs = config.get("sheet_configs", {})
            sheet_categories = config.get("sheet_categories", {})
            circular_groups = config.get("circular_groups", [])

            tm.append_log(task_id, f"使用配置：{len(sheet_configs)} 个sheet", step=2)
            check_stop()

            # ── Phase 1: extract indicators (0.05 → 0.40) ──────────────────
            update("提取指标名称和公式...", 0.05)

            def indicators_cb(msg: str, pct: float) -> None:
                check_stop()
                update(msg, 0.05 + pct * 0.35)

            indicators = extract_indicators(
                excel_path,
                sheet_configs=sheet_configs,
                sheet_categories=sheet_categories,
                progress_callback=indicators_cb,
            )
            tm.append_log(task_id, f"提取到 {len(indicators)} 个指标", step=2)
            check_stop()

            # ── Phase 2: extract values (0.40 → 0.85) ──────────────────────
            update("开始提取数值（逐sheet流式读取）...", 0.40)

            def values_cb(msg: str, pct: float) -> None:
                update(msg, 0.40 + pct * 0.45)

            indicators = extract_values(
                excel_path,
                indicators,
                sheet_configs=sheet_configs,
                progress_callback=values_cb,
                stop_check=check_stop,
            )
            tm.append_log(task_id, "数值提取完成", step=2)
            check_stop()

            # ── Phase 3: parse dependencies (0.85 → 0.95) ──────────────────
            update("解析公式依赖关系...", 0.85)
            edges = parse_dependencies(indicators, circular_groups=circular_groups)
            tm.append_log(task_id, f"解析到 {len(edges)} 条依赖边", step=2)

            # ── Save ────────────────────────────────────────────────────────
            update("保存结果...", 0.95)
            from src.parser.indicator_registry import save_indicators
            from src.parser.formula_parser import save_dependencies
            save_indicators(indicators, tm.get_indicators_path(task_id))
            save_dependencies(edges, tm.get_dependencies_path(task_id))

            # ── Phase 4: coverage scan (0.95 → 1.0) ────────────────────────
            update("运行覆盖率扫描...", 0.95)
            from src.parser.coverage_scanner import scan_coverage, save_coverage
            coverage = scan_coverage(excel_path, sheet_configs, indicators)
            save_coverage(coverage, tm.get_coverage_path(task_id))
            s = coverage["summary"]
            tm.append_log(
                task_id,
                f"覆盖率: {s['extracted_rows']}/{s['total_content_rows']} "
                f"({s['coverage_pct']:.1%})，断裂依赖: {s['broken_deps']}",
                step=2,
            )

            elapsed = time.time() - start_time
            done_msg = f"解析完成：{len(indicators)} 指标，{len(edges)} 依赖 | 总用时 {_fmt_time(elapsed)}"
            tm.append_log(task_id, done_msg, step=2)
            meta.step2 = StepInfo(status="done", progress_msg=done_msg, progress_pct=1.0)
            meta.indicator_count = len(indicators)
            meta.edge_count = len(edges)
            tm.save_task(meta)

        except _StopRequested:
            elapsed = time.time() - start_time
            tm.append_log(task_id, f"Step 2 已停止（已用时 {_fmt_time(elapsed)}）", step=2)
            meta.step2 = StepInfo(status="error", error="用户停止", progress_msg="已停止")
            tm.save_task(meta)
        except Exception as e:
            import traceback
            tm.append_log(task_id, f"Step 2 错误: {e}\n{traceback.format_exc()}", step=2)
            meta.step2 = StepInfo(status="error", error=str(e), progress_msg=str(e))
            tm.save_task(meta)

    # ------------------------------------------------------------------ #
    # Step 3: Load to Neo4j
    # ------------------------------------------------------------------ #

    def run_step3(self, task_id: str, neo4j_uri: str, neo4j_user: str, neo4j_password: str) -> None:
        key = f"{task_id}_step3"
        if self.is_running(task_id, 3):
            return
        stop_ev = self._make_stop_event(task_id, 3)
        t = threading.Thread(
            target=self._step3_worker,
            args=(task_id, neo4j_uri, neo4j_user, neo4j_password, stop_ev),
            daemon=True,
        )
        self._threads[key] = t
        t.start()

    def _step3_worker(self, task_id: str, uri: str, user: str, password: str, stop_ev: threading.Event) -> None:
        tm = self.tm
        check_stop = self._make_stop_checker(stop_ev, task_id, 3)
        meta = tm.get_task(task_id)
        meta.step3 = StepInfo(status="running", progress_msg="连接Neo4j...", progress_pct=0.1)
        tm.save_task(meta)
        tm.clear_log(task_id, step=3)
        tm.append_log(task_id, "Step 3 开始：加载到Neo4j", step=3)

        try:
            import json as _json
            from src.graph.loader import GraphLoader

            indicators = _json.loads(tm.get_indicators_path(task_id).read_text(encoding="utf-8"))
            edges = _json.loads(tm.get_dependencies_path(task_id).read_text(encoding="utf-8"))
            tm.append_log(task_id, f"加载数据：{len(indicators)} 指标，{len(edges)} 边", step=3)
            check_stop()

            meta.step3.progress_msg = "加载数据到Neo4j..."
            meta.step3.progress_pct = 0.3
            tm.save_task(meta)

            with GraphLoader(uri, user, password, task_id=task_id) as loader:
                loader.load_all(indicators, edges)

            tm.append_log(task_id, "Neo4j加载完成", step=3)
            meta.step3 = StepInfo(status="done", progress_msg="加载完成", progress_pct=1.0)
            tm.save_task(meta)

        except _StopRequested:
            tm.append_log(task_id, "Step 3 已停止", step=3)
            meta.step3 = StepInfo(status="error", error="用户停止", progress_msg="已停止")
            tm.save_task(meta)
        except Exception as e:
            import traceback
            tm.append_log(task_id, f"Step 3 错误: {e}\n{traceback.format_exc()}", step=3)
            meta.step3 = StepInfo(status="error", error=str(e), progress_msg=str(e))
            tm.save_task(meta)

    def clear_neo4j_task(self, task_id: str, uri: str, user: str, password: str) -> None:
        from src.graph.loader import GraphLoader
        with GraphLoader(uri, user, password, task_id=task_id) as loader:
            loader.clear_task_data()
