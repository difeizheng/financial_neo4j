"""
pages/3_modify_params.py — Parameter modification page.

Allows users to edit parameter values from 参数输入表,
recalculate the model via the formulas engine, and persist changes to Neo4j.
"""
from __future__ import annotations

import logging
import sys
import json
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st

import config
from src.task.manager import TaskManager

logger = logging.getLogger(__name__)

st.set_page_config(page_title="修改参数值", page_icon="✏️", layout="wide")


# ── Page guard ──────────────────────────────────────────────────────────────────

def get_task_manager() -> TaskManager:
    if "task_manager" not in st.session_state:
        st.session_state.task_manager = TaskManager(config.TASKS_DIR)
    return st.session_state.task_manager


task_id = st.session_state.get("current_task_id")
if not task_id:
    st.warning("请先从任务列表选择一个任务。")
    if st.button("前往任务列表"):
        st.switch_page("pages/1_task_list.py")
    st.stop()

tm = get_task_manager()
meta = tm.get_task(task_id)
if not meta:
    st.error("任务不存在。")
    st.stop()

# Require Step 3 to be done
if meta.step3.status != "done":
    st.error("请先完成 Step 3（加载到Neo4j）后再修改参数。")
    if st.button("返回任务详情"):
        st.switch_page("pages/2_task_detail.py")
    st.stop()


# ── Load data ──────────────────────────────────────────────────────────────────

@st.cache_data
def load_indicators(task_id: str):
    path = tm.get_indicators_path(task_id)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_data
def load_sheet_configs(task_id: str):
    path = tm.get_config_path(task_id)
    if not path.exists():
        return {}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return cfg.get("sheet_configs", {})


indicators = load_indicators(task_id)
sheet_configs = load_sheet_configs(task_id)

# Filter: is_input=True, formula_raw does NOT start with '='
def is_editable(ind: dict) -> bool:
    fr = ind.get("formula_raw") or ""
    return bool(ind.get("is_input")) and not str(fr).startswith("=")


editable_params = [ind for ind in indicators if is_editable(ind)]

# Sort by Excel row number (序号)
editable_params.sort(key=lambda ind: ind.get("row") or 0)

# Group by category
def group_by_category(params):
    groups = {}
    for p in params:
        cat = p.get("category") or p.get("sheet_category") or "未分类"
        groups.setdefault(cat, []).append(p)
    return groups


groups = group_by_category(editable_params)
categories = sorted(groups.keys())


# ── Session state for changes ──────────────────────────────────────────────────

def _init_change_state():
    if "param_changes" not in st.session_state:
        st.session_state.param_changes = {}       # {ind_id: new_value}
    if "param_overrides" not in st.session_state:
        # Load existing overrides if any
        override_path = tm.get_param_overrides_path(task_id)
        if override_path.exists():
            try:
                st.session_state.param_overrides = json.loads(
                    override_path.read_text(encoding="utf-8")
                )
            except Exception:
                st.session_state.param_overrides = {}
        else:
            st.session_state.param_overrides = {}


_init_change_state()


# ── Snapshot helpers ────────────────────────────────────────────────────────────

def _snapshot_path():
    return tm.get_param_snapshot_path(task_id)


def _overrides_path():
    return tm.get_param_overrides_path(task_id)


def _has_snapshot() -> bool:
    return _snapshot_path().exists()


def _save_overrides(overrides: dict):
    """Atomically write overrides to disk."""
    path = _overrides_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(overrides, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_overrides() -> dict:
    path = _overrides_path()
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


# ── Value helpers ──────────────────────────────────────────────────────────────

def _to_float(val) -> float | None:
    """Safely convert a value to float, returning None on failure."""
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


def get_current_value(ind: dict) -> float:
    """Get the current (possibly overridden) value for an indicator."""
    ind_id = ind["id"]
    overrides = _load_overrides()
    if ind_id in overrides:
        vals = overrides[ind_id]
        if isinstance(vals, list) and vals:
            v = _to_float(vals[0])
            if v is not None:
                return v
        v = _to_float(vals)
        if v is not None:
            return v
    vals = ind.get("values_json") or []
    if isinstance(vals, list) and vals:
        v = _to_float(vals[0])
        if v is not None:
            return v
    v = _to_float(ind.get("value_year1"))
    return v if v is not None else 0.0


def get_all_years(ind: dict) -> list:
    """Get the current 48-year values (with overrides applied)."""
    ind_id = ind["id"]
    overrides = _load_overrides()
    if ind_id in overrides:
        vals = overrides[ind_id]
        if isinstance(vals, list) and len(vals) == 48:
            return [_to_float(v) if v is not None else None for v in vals]
        if isinstance(vals, list) and vals:
            return vals  # single value, return as-is for display
    vals = ind.get("values_json") or []
    if isinstance(vals, list) and len(vals) == 48:
        return [_to_float(v) if v is not None else None for v in vals]
    # Fallback: single value
    first = _to_float(ind.get("value_year1"))
    return [first if first is not None else 0.0]


# ── UI render helpers ──────────────────────────────────────────────────────────

def _render_indicator_row(ind: dict):
    """Render a single indicator with editable value input and apply button."""
    ind_id = ind["id"]
    name = ind.get("name", ind_id)
    unit = ind.get("unit", "")
    current = get_current_value(ind)
    seq = ind.get("row") or 0

    has_years = len(get_all_years(ind)) > 1

    with st.container():
        col_seq, col_name, col_val, col_unit, col_btn = st.columns([1, 3, 2, 1, 1])
        with col_seq:
            st.caption(f"#{seq}")
        with col_name:
            st.markdown(f"**{name}**")
        with col_val:
            new_val = st.number_input(
                "值",
                value=float(current) if current is not None else 0.0,
                format="%.6g",
                key=f"val_{ind_id}",
                label_visibility="collapsed",
            )
        with col_unit:
            st.caption(unit or "—")
        with col_btn:
            st.write("")  # spacer
            if st.button("✓ 应用", key=f"apply_{ind_id}"):
                if new_val != st.session_state.param_changes.get(ind_id):
                    st.session_state.param_changes[ind_id] = new_val
                    st.rerun()

    # Show year series expander for multi-year indicators
    if has_years:
        with st.expander("📅 展开年度序列 (48年)"):
            years = get_all_years(ind)
            # Show in batches of 12 years per row
            for batch_start in range(0, 48, 12):
                batch_years = years[batch_start:batch_start + 12]
                cols = st.columns(12)
                for i, (col, val) in enumerate(zip(cols, batch_years)):
                    with col:
                        st.text(f"Y{batch_start+i+1}")
                        st.text(f"{val:.4g}" if val is not None else "—")

    st.divider()


def _render_category(params: list):
    """Render all indicators in a category as editable rows."""
    for ind in params:
        _render_indicator_row(ind)


# ── Action functions ────────────────────────────────────────────────────────────

def _do_save_and_recalculate():
    """Run recalculation in a background thread, update Neo4j on completion."""
    changes = st.session_state.param_changes
    if not changes:
        return

    # 1. Take snapshot on first save
    if not _has_snapshot():
        _take_snapshot()

    # 2. Save overrides immediately (so they persist if page refreshes)
    overrides = _load_overrides()
    for ind_id, new_val in changes.items():
        overrides[ind_id] = [new_val]
    _save_overrides(overrides)
    st.session_state.param_changes = {}

    # 3. Run recalculation in background
    st.info("⏳ 正在重算（约 10-30 秒）...")
    progress_placeholder = st.empty()
    results_placeholder = st.empty()

    def recalc_thread():
        try:
            from src.graph.recalculator import ParameterRecalculator

            progress_msgs = []

            def report(msg: str):
                progress_msgs.append(msg)

            recalc = ParameterRecalculator(
                task_id=task_id,
                excel_path=tm.get_excel_path(task_id),
                indicators=indicators,
                sheet_configs=sheet_configs,
            )

            new_values = recalc.recalculate(
                changes=changes,
                progress_callback=report,
            )

            # Update Neo4j
            from src.graph.loader import GraphLoader
            with GraphLoader(
                config.NEO4J_URI,
                config.NEO4J_USER,
                config.NEO4J_PASSWORD,
                task_id=task_id,
            ) as loader:
                loader.update_indicator_values(new_values)

            # Save overrides with all values
            full_overrides = _load_overrides()
            for ind_id, vals in new_values.items():
                full_overrides[ind_id] = vals
            _save_overrides(full_overrides)

            st.session_state["_recalc_success"] = True
            st.session_state["_recalc_count"] = len(new_values)
            st.session_state["_recalc_msgs"] = progress_msgs

        except Exception as e:
            st.session_state["_recalc_error"] = str(e)

    t = threading.Thread(target=recalc_thread, daemon=True)
    t.start()

    # Poll for completion
    import time
    for _ in range(60):  # max 60 seconds
        time.sleep(1)
        if st.session_state.get("_recalc_success") or st.session_state.get("_recalc_error"):
            break
        # Show latest progress message
        msgs = st.session_state.get("_recalc_msgs", [])
        if msgs:
            st.info(f"⏳ {msgs[-1]}")
        st.rerun()

    # Show result
    if st.session_state.pop("_recalc_success", False):
        count = st.session_state.pop("_recalc_count", 0)
        st.success(f"✅ 重算完成，已更新 {count} 个指标的值！")
        st.session_state.pop("_recalc_msgs", None)
        # Clear widget cache to show new values
        st.cache_data.clear()
        st.rerun()
    elif st.session_state.get("_recalc_error"):
        err = st.session_state.pop("_recalc_error")
        st.error(f"❌ 重算失败: {err}")
        st.session_state.pop("_recalc_msgs", None)


def _take_snapshot():
    """Save original values to snapshot file."""
    snapshot = {}
    for ind in indicators:
        vals = ind.get("values_json") or []
        snapshot[ind["id"]] = vals if vals else [ind.get("value_year1")]

    path = _snapshot_path()
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Snapshot saved to {path}")


def _do_restore_original():
    """Restore original values from snapshot and update Neo4j."""
    snapshot_path = _snapshot_path()
    if not snapshot_path.exists():
        st.warning("没有找到原始快照。")
        return

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))

    try:
        from src.graph.loader import GraphLoader
        with GraphLoader(
            config.NEO4J_URI,
            config.NEO4J_USER,
            config.NEO4J_PASSWORD,
            task_id=task_id,
        ) as loader:
            loader.update_indicator_values(snapshot)

        # Clear overrides
        overrides_path = _overrides_path()
        if overrides_path.exists():
            overrides_path.unlink()

        st.session_state.param_changes = {}
        st.session_state.pop("_snapshot_shown", None)

        st.success("✅ 已恢复到原始值，Neo4j 已更新！")
        st.cache_data.clear()
        st.rerun()

    except Exception as e:
        st.error(f"恢复失败: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# PAGE RENDER — all helper functions must be defined above this point
# ═══════════════════════════════════════════════════════════════════════════════

# ── Page header ────────────────────────────────────────────────────────────────

st.title(f"✏️ 修改参数值 — {meta.name}")
st.caption(f"任务ID: {task_id[:8]}...  |  可编辑参数: {len(editable_params)} 个")

if st.button("← 返回任务详情"):
    st.switch_page("pages/2_task_detail.py")

st.divider()

if not editable_params:
    st.info("参数输入表中没有找到可编辑的参数（is_input=True 且无公式）。")
    st.stop()

# ── Snapshot banner ─────────────────────────────────────────────────────────────

if _has_snapshot() and not st.session_state.get("_snapshot_shown"):
    st.info("📸 已存在参数快照。当前显示的是最新已保存值。点击「↩ 恢复原始值」可撤销所有修改。")
    st.session_state["_snapshot_shown"] = True

# ── Layout: left = editors, right = change log ─────────────────────────────────

col_editor, col_actions = st.columns([4, 1])

with col_editor:
    # ── Category tabs ──────────────────────────────────────────────────────────
    if len(categories) == 1:
        # Single category: render directly (no tabs)
        _render_category(groups[categories[0]])
    elif len(categories) > 1:
        # Multiple categories: one tab per category
        tabs = st.tabs(categories)
        for cat, tab in zip(categories, tabs):
            with tab:
                _render_category(groups[cat])


with col_actions:
    st.markdown("### 已修改")
    changes = st.session_state.param_changes

    if changes:
        for ind_id, new_val in changes.items():
            ind = next((i for i in editable_params if i["id"] == ind_id), None)
            if ind:
                old = get_current_value(ind)
                st.markdown(
                    f"**{ind['name']}**\n"
                    f"`{old}` → `{new_val}`"
                )
        st.divider()
    else:
        st.caption("尚未修改任何参数")

    # Action buttons
    col_save, col_restore = st.columns(2)

    with col_save:
        save_disabled = not changes
        if st.button(
            "💾 保存并重算",
            disabled=save_disabled,
            type="primary",
        ):
            _do_save_and_recalculate()

    with col_restore:
        restore_disabled = not _has_snapshot()
        if st.button("↩ 恢复原始值", disabled=restore_disabled):
            _do_restore_original()
