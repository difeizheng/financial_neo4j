"""
pages/3_modify_params.py — Parameter modification page (重构版 v2.5.0)

Features:
- 任务下拉框（顶部独立一行）
- 左右 5:5 分栏布局
- 左侧：指标编辑区（树形层级、类别分组）
- 右侧：修改日志 + 影响预览（Tab 切换）
- 试算管理（创建/保存/恢复）
"""
from __future__ import annotations

import logging
import sys
import json
import threading
import time
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

import config
from src.task.manager import TaskManager
from src.trial.store import TrialStore
from src.trial.manager import TrialManager
from src.ui.change_log import render_change_log
from src.ui.impact_preview import render_impact_preview

logger = logging.getLogger(__name__)

st.set_page_config(page_title="修改参数值", page_icon="✏️", layout="wide")


# ── Managers ──────────────────────────────────────────────────────────────────

def get_task_manager() -> TaskManager:
    if "task_manager" not in st.session_state:
        st.session_state.task_manager = TaskManager(config.TASKS_DIR)
    return st.session_state.task_manager


def get_trial_store() -> TrialStore:
    if "trial_store" not in st.session_state:
        st.session_state.trial_store = TrialStore(config.TRIALS_DB)
    return st.session_state.trial_store


def get_trial_manager() -> TrialManager:
    if "trial_manager" not in st.session_state:
        st.session_state.trial_manager = TrialManager(
            get_trial_store(), get_task_manager()
        )
    return st.session_state.trial_manager


tm = get_task_manager()
trial_store = get_trial_store()
trial_manager = get_trial_manager()


# ── Task selector ──────────────────────────────────────────────────────────────

def render_task_selector() -> str | None:
    """渲染任务下拉框，返回选中的 task_id。"""
    all_tasks = tm.list_tasks()
    # 只显示 Step 3 完成的任务（已加载到 Neo4j）
    valid_tasks = [t for t in all_tasks if t.step3.status == "done"]

    if not valid_tasks:
        st.warning("没有已加载到 Neo4j 的任务。请先在任务详情页完成 Step 3。")
        if st.button("前往任务列表"):
            st.switch_page("pages/1_task_list.py")
        st.stop()

    options = {t.task_id: f"{t.name} ({t.task_id[:8]}...)" for t in valid_tasks}
    options[""] = "— 选择任务 —"

    current_task_id = st.session_state.get("current_task_id", "")

    # 确定默认选中项
    keys = list(options.keys())
    default_index = 0 if not current_task_id else (
        keys.index(current_task_id) if current_task_id in keys else 0
    )

    selected = st.selectbox(
        "选择任务",
        options=keys,
        format_func=lambda k: options.get(k, k),
        index=default_index,
        key="task_selector_dropdown",
        label_visibility="collapsed",
    )

    if selected and selected != current_task_id:
        # 切换任务时清理状态
        st.session_state.current_task_id = selected
        st.session_state.param_changes = {}
        st.session_state._pending_trial_id = None
        st.session_state.needs_recalc = False
        st.cache_data.clear()
        st.rerun()

    return selected if selected else None


task_id = render_task_selector()

if not task_id:
    st.info("请在上方选择一个任务。")
    st.stop()

meta = tm.get_task(task_id)
if not meta:
    st.error("任务不存在。")
    st.stop()


# ── Load data ──────────────────────────────────────────────────────────────────

@st.cache_data
def load_indicators_cached(task_id: str):
    path = tm.get_indicators_path(task_id)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data
def load_sheet_configs_cached(task_id: str):
    path = tm.get_config_path(task_id)
    if not path.exists():
        return {}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return cfg.get("sheet_configs", {})


indicators = load_indicators_cached(task_id)
sheet_configs = load_sheet_configs_cached(task_id)


# ── Filter editable params ─────────────────────────────────────────────────────

def is_editable(ind: dict) -> bool:
    fr = ind.get("formula_raw") or ""
    return bool(ind.get("is_input")) and not str(fr).startswith("=")


editable_params = [ind for ind in indicators if is_editable(ind)]
editable_params.sort(key=lambda ind: ind.get("row") or 0)


def group_by_category(params):
    groups: dict[str, list] = {}
    for p in params:
        cat = p.get("category") or p.get("sheet_category") or "未分类"
        groups.setdefault(cat, []).append(p)
    return groups


groups = group_by_category(editable_params)
categories = sorted(
    groups.keys(),
    key=lambda cat: min(p.get("row", 0) for p in groups[cat]),
)


# ── Session state ─────────────────────────────────────────────────────────────

def _init_state():
    if "param_changes" not in st.session_state:
        st.session_state.param_changes = {}
    if "_pending_trial_id" not in st.session_state:
        st.session_state._pending_trial_id = None
    if "needs_recalc" not in st.session_state:
        st.session_state.needs_recalc = False


_init_state()

changes = st.session_state.param_changes
pending_trial_id = st.session_state._pending_trial_id
needs_recalc = st.session_state.needs_recalc


# ── Hidden indicators ──────────────────────────────────────────────────────────

def _load_hidden() -> set:
    path = tm.get_hidden_indicators_path(task_id)
    if path.exists():
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def _save_hidden(hidden: set):
    path = tm.get_hidden_indicators_path(task_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(list(hidden), ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _toggle_hidden(ind_id: str):
    hidden = _load_hidden()
    if ind_id in hidden:
        hidden.discard(ind_id)
    else:
        hidden.add(ind_id)
    _save_hidden(hidden)
    st.rerun()


hidden_set = _load_hidden()


# ── Value helpers ──────────────────────────────────────────────────────────────

def _to_float(val) -> float | None:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
    return None


def get_current_value(ind: dict):
    ind_id = ind["id"]
    # 先检查 pending changes
    if ind_id in changes:
        return changes[ind_id]
    # 再检查 overrides
    override_path = tm.get_param_overrides_path(task_id)
    if override_path.exists():
        try:
            overrides = json.loads(override_path.read_text(encoding="utf-8"))
            if ind_id in overrides:
                vals = overrides[ind_id]
                if isinstance(vals, list) and vals:
                    return vals[0]
                return vals
        except Exception:
            pass
    # 最后从原始数据读取
    vals_raw = ind.get("values_json") or []
    if isinstance(vals_raw, str):
        try:
            vals_raw = json.loads(vals_raw)
        except Exception:
            vals_raw = []
    if isinstance(vals_raw, list) and vals_raw:
        return vals_raw[0]
    return ind.get("value_year1")


def get_all_years(ind: dict) -> list:
    ind_id = ind["id"]
    override_path = tm.get_param_overrides_path(task_id)
    if override_path.exists():
        try:
            overrides = json.loads(override_path.read_text(encoding="utf-8"))
            if ind_id in overrides:
                vals = overrides[ind_id]
                if isinstance(vals, list) and len(vals) == 48:
                    return [_to_float(v) if v is not None else None for v in vals]
                if isinstance(vals, list) and vals:
                    return vals
        except Exception:
            pass
    vals_raw = ind.get("values_json") or []
    if isinstance(vals_raw, str):
        try:
            vals_raw = json.loads(vals_raw)
        except Exception:
            vals_raw = []
    if isinstance(vals_raw, list) and len(vals_raw) == 48:
        return [_to_float(v) if v is not None else None for v in vals_raw]
    first = _to_float(ind.get("value_year1"))
    return [first if first is not None else 0.0]


# ── Input type detection ───────────────────────────────────────────────────────

def _detect_input_type(ind: dict) -> str:
    formula = str(ind.get("formula_raw") or "")
    unit = ind.get("unit") or ""
    value = ind.get("value_year1")

    if "00:00:00" in formula:
        return "date"
    if unit == "%":
        return "percent"
    if value is not None and isinstance(value, str) and _to_float(value) is None:
        return "text"
    return "number"


def _parse_date_value(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, date):
        return val
    s = str(val)
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


# ── UI render helpers ──────────────────────────────────────────────────────────

def _render_indicator_row(ind: dict, depth: int = 0):
    ind_id = ind["id"]
    name = ind.get("name", ind_id)
    unit = ind.get("unit") or ""
    seq = ind.get("section_number") or str(ind.get("row") or "")
    input_type = _detect_input_type(ind)
    current = get_current_value(ind)

    if depth == 0:
        col_seq, col_name, col_input, col_unit, col_btn = st.columns([1, 3, 2, 1, 1])
    else:
        col_indent, col_seq, col_name, col_input, col_unit, col_btn = st.columns([0.5, 1, 3, 2, 1, 1])
        with col_indent:
            st.markdown("&nbsp;", unsafe_allow_html=True)

    with col_seq:
        st.caption(seq)

    with col_name:
        if depth > 0:
            st.markdown(f"↳ {name}")
        else:
            st.markdown(f"**{name}**")

    with col_input:
        if input_type == "date":
            parsed = _parse_date_value(current)
            default_date = parsed if parsed else date(2023, 1, 1)
            new_val = st.date_input(
                "日期",
                value=default_date,
                key=f"val_{ind_id}",
                label_visibility="collapsed",
            )
            new_val_store = new_val.strftime("%Y-%m-%d") if new_val else None

        elif input_type == "percent":
            float_val = _to_float(current) or 0.0
            float_val = max(0.0, min(1.0, float_val))
            pct_display = round(float_val * 100, 1)
            new_pct = st.slider(
                "%",
                min_value=0.0,
                max_value=100.0,
                value=pct_display,
                step=0.1,
                format="%.1f%%",
                key=f"val_{ind_id}",
                label_visibility="collapsed",
            )
            new_val = new_pct / 100.0
            new_val_store = new_val

        elif input_type == "text":
            st.caption(str(current) if current is not None else "—")
            new_val_store = None

        else:
            float_val = _to_float(current)
            new_val = st.number_input(
                "值",
                value=float(float_val) if float_val is not None else 0.0,
                format="%.6g",
                key=f"val_{ind_id}",
                label_visibility="collapsed",
            )
            new_val_store = new_val

    with col_unit:
        st.caption(unit or "—")

    with col_btn:
        if st.button("🙈", key=f"hide_{ind_id}", help="隐藏此指标"):
            _toggle_hidden(ind_id)

        if input_type != "text" and new_val_store is not None:
            if st.button("✓", key=f"apply_{ind_id}", help="应用修改"):
                # 记录修改
                old_val = get_current_value(ind)
                st.session_state.param_changes[ind_id] = new_val_store

                # 确保有一个 pending trial
                if not st.session_state._pending_trial_id:
                    st.session_state._pending_trial_id = trial_manager.get_or_create_draft(task_id)

                # 写入修改日志
                trial_id = st.session_state._pending_trial_id
                trial_store.upsert_change_log(
                    trial_id=trial_id,
                    indicator_id=ind_id,
                    indicator_name=name,
                    old_value=_to_float(old_val),
                    new_value=_to_float(new_val_store),
                )
                st.rerun()

    if input_type == "number":
        years = get_all_years(ind)
        if len(years) > 1:
            with st.expander(f"📅 年度序列 ({len(years)}年)"):
                for batch_start in range(0, len(years), 12):
                    batch = years[batch_start:batch_start + 12]
                    cols = st.columns(len(batch))
                    for i, (col, val) in enumerate(zip(cols, batch)):
                        with col:
                            st.text(f"Y{batch_start+i+1}")
                            st.text(f"{val:.4g}" if val is not None else "—")

    st.divider()


def _render_hidden_row(ind: dict):
    ind_id = ind["id"]
    name = ind.get("name", ind_id)
    unit = ind.get("unit") or ""
    seq = ind.get("section_number") or str(ind.get("row") or "")

    col_seq, col_name, col_unit, col_btn = st.columns([1, 5, 1, 1])
    with col_seq:
        st.caption(seq)
    with col_name:
        st.markdown(f"<span style='color:gray'>~~{name}~~</span>", unsafe_allow_html=True)
    with col_unit:
        st.caption(unit or "—")
    with col_btn:
        if st.button("👁", key=f"show_{ind_id}", help="显示此指标"):
            _toggle_hidden(ind_id)


def _render_category(params: list):
    roots = [p for p in params if not p.get("parent_id")]
    children_map: dict[str, list] = {}
    for p in params:
        pid = p.get("parent_id")
        if pid:
            children_map.setdefault(pid, []).append(p)

    for root in roots:
        root_id = root["id"]
        if root_id in hidden_set:
            _render_hidden_row(root)
        else:
            _render_indicator_row(root, depth=0)

        for child in children_map.get(root_id, []):
            child_id = child["id"]
            if child_id in hidden_set:
                col_indent, col_rest = st.columns([0.5, 11.5])
                with col_rest:
                    _render_hidden_row(child)
            else:
                _render_indicator_row(child, depth=1)

    root_ids = {r["id"] for r in roots}
    for p in params:
        pid = p.get("parent_id")
        if pid and pid not in root_ids:
            if p["id"] in hidden_set:
                _render_hidden_row(p)
            else:
                _render_indicator_row(p, depth=0)


# ── Page layout ────────────────────────────────────────────────────────────────

st.title(f"✏️ 修改参数值 — {meta.name}")

st.caption(
    f"任务ID: {task_id[:8]}...  |  可编辑参数: {len(editable_params)} 个"
    + (f"  |  已隐藏: {len(hidden_set)} 个" if hidden_set else "")
)

if st.button("← 返回任务详情"):
    st.switch_page("pages/2_task_detail.py")

st.divider()

if not editable_params:
    st.info("没有可编辑的参数。")
    st.stop()

# ── Main layout: 5:5 ───────────────────────────────────────────────────────────

col_left, col_right = st.columns([1, 1])

with col_left:
    st.subheader("参数编辑")
    if len(categories) == 1:
        _render_category(groups[categories[0]])
    else:
        tabs = st.tabs(categories)
        for cat, tab in zip(categories, tabs):
            with tab:
                _render_category(groups[cat])

with col_right:
    tab_log, tab_preview = st.tabs(["修改日志", "影响预览"])

    with tab_log:
        # 获取修改日志
        if pending_trial_id:
            logs = trial_store.get_change_logs(pending_trial_id, include_deleted=False)
        else:
            logs = []

        def on_delete_log(log_id: int, indicator_id: str):
            # 软删除日志
            trial_store.mark_log_deleted(log_id)
            # 从 param_changes 中移除
            if indicator_id in st.session_state.param_changes:
                del st.session_state.param_changes[indicator_id]
            # 标记需要重算
            st.session_state.needs_recalc = True
            st.rerun()

        render_change_log(
            logs=logs,
            on_delete=on_delete_log,
            needs_recalc=needs_recalc,
        )

    with tab_preview:
        changed_ids = list(changes.keys())
        render_impact_preview(
            task_id=task_id,
            changed_indicator_ids=changed_ids,
            neo4j_uri=config.NEO4J_URI,
            neo4j_user=config.NEO4J_USER,
            neo4j_password=config.NEO4J_PASSWORD,
        )

# ── Action buttons ─────────────────────────────────────────────────────────────

st.divider()

col_save, col_restore, col_trials = st.columns([2, 2, 3])

with col_save:
    if st.button("💾 保存并重算", disabled=not changes, type="primary"):
        # 创建试算
        if not pending_trial_id:
            pending_trial_id = trial_manager.get_or_create_draft(task_id)
            st.session_state._pending_trial_id = pending_trial_id

        # 启动后台重算
        trial_manager.start_recalculation(
            trial_id=pending_trial_id,
            task_id=task_id,
            changes=changes,
        )
        st.session_state.param_changes = {}
        st.session_state._recalc_running = True
        st.session_state._recalc_trial_id = pending_trial_id
        st.rerun()

with col_restore:
    snapshot_path = tm.get_param_snapshot_path(task_id)
    if st.button("↩ 恢复原始值", disabled=not snapshot_path.exists()):
        try:
            trial_manager.restore_original_values(task_id)
            st.session_state.param_changes = {}
            st.session_state._pending_trial_id = None
            st.session_state.needs_recalc = False
            st.cache_data.clear()
            st.success("✅ 已恢复原始值")
            st.rerun()
        except Exception as e:
            st.error(f"恢复失败: {e}")

with col_trials:
    trials = trial_store.get_trials(task_id)
    done_trials = [t for t in trials if t["status"] == "done"]
    st.caption(f"已完成 {len(done_trials)} 个试算")
    if st.button("管理试算", key="manage_trials_btn"):
        st.session_state._show_trial_manager = True
        st.rerun()

# ── Trial Management (条件渲染) ─────────────────────────────────────────────────

if st.session_state.get("_show_trial_manager"):
    st.divider()
    from src.ui.trial_comparison import render_trial_management, render_trial_comparison

    tab_list, tab_compare = st.tabs(["试算列表", "试算对比"])

    with tab_list:
        render_trial_management(task_id, trial_store, trial_manager)

    with tab_compare:
        render_trial_comparison(task_id, trial_store, tm)

    if st.button("关闭试算管理", key="close_trial_manager"):
        st.session_state._show_trial_manager = False
        st.rerun()

# ── Recalculation progress ─────────────────────────────────────────────────────

if st.session_state.get("_recalc_running"):
    recalc_trial_id = st.session_state.get("_recalc_trial_id")
    if recalc_trial_id:
        trial = trial_store.get_trial(recalc_trial_id)
        if trial:
            status = trial["status"]

            # 显示进度条
            progress_bar = st.progress(0, text="正在重算...")

            if status == "done":
                # 完成
                progress_bar.progress(100, text="重算完成！")
                st.session_state._recalc_running = False
                st.session_state._show_results = True
                st.session_state._result_trial_id = recalc_trial_id
                st.success("✅ 重算完成！")
                time.sleep(1)
                st.rerun()

            elif status == "error":
                # 错误
                progress_bar.empty()
                st.session_state._recalc_running = False
                st.error(f"❌ 重算失败: {trial.get('error_msg', '未知错误')}")
                st.rerun()

            elif status in ("running", "pending"):
                # 还在运行
                # 检查线程是否真的还在运行
                thread_alive = trial_manager.is_running(recalc_trial_id)

                if not thread_alive and status == "running":
                    # 线程已结束但状态还是 running，可能是线程崩溃
                    # 强制刷新状态
                    st.session_state._recalc_running = False
                    st.warning("⚠️ 重算线程异常终止，请检查日志")
                    st.rerun()

                # 继续等待
                progress_bar.progress(50, text=f"正在重算... (状态: {status})")
                time.sleep(3)
                st.rerun()

            else:
                # 其他状态
                progress_bar.empty()
                st.session_state._recalc_running = False
                st.warning(f"⚠️ 未知状态: {status}")
                st.rerun()
        else:
            # trial 不存在
            st.session_state._recalc_running = False
            st.error("❌ 试算记录丢失")
            st.rerun()

# ── Results display ────────────────────────────────────────────────────────────

if st.session_state.get("_show_results"):
    result_trial_id = st.session_state.get("_result_trial_id")
    if result_trial_id:
        st.divider()
        from src.ui.trial_results import render_trial_results
        render_trial_results(
            trial_id=result_trial_id,
            task_id=task_id,
            trial_store=trial_store,
            task_manager=tm,
        )