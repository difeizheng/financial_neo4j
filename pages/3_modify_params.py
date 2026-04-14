"""
pages/3_modify_params.py — Parameter modification page.

Allows users to edit parameter values from 参数输入表,
recalculate the model via the formulas engine, and persist changes to Neo4j.

Features:
- Categories ordered by Excel row number
- Indicators ordered by row within each category
- Tree-indented parent/child hierarchy
- Input type auto-detection: date / percent (slider) / text (read-only) / number
- Per-indicator hide/show toggle, persisted to hidden_indicators.json
"""
from __future__ import annotations

import logging
import sys
import json
import threading
from datetime import date, datetime
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

# Sort by Excel row number
editable_params.sort(key=lambda ind: ind.get("row") or 0)

# Group by category
def group_by_category(params):
    groups: dict[str, list] = {}
    for p in params:
        cat = p.get("category") or p.get("sheet_category") or "未分类"
        groups.setdefault(cat, []).append(p)
    return groups


groups = group_by_category(editable_params)

# Sort categories by the minimum row number of their indicators (Excel order)
categories = sorted(
    groups.keys(),
    key=lambda cat: min(p.get("row", 0) for p in groups[cat]),
)


# ── Session state for changes ──────────────────────────────────────────────────

def _init_change_state():
    if "param_changes" not in st.session_state:
        st.session_state.param_changes = {}       # {ind_id: new_value}
    if "param_overrides" not in st.session_state:
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


# ── Snapshot helpers ────────────────────────────────────────────────────────────

def _snapshot_path():
    return tm.get_param_snapshot_path(task_id)


def _overrides_path():
    return tm.get_param_overrides_path(task_id)


def _has_snapshot() -> bool:
    return _snapshot_path().exists()


def _save_overrides(overrides: dict):
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
    """Get the current (possibly overridden) value for an indicator."""
    ind_id = ind["id"]
    overrides = _load_overrides()
    if ind_id in overrides:
        vals = overrides[ind_id]
        if isinstance(vals, list) and vals:
            return vals[0]
        return vals
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
    overrides = _load_overrides()
    if ind_id in overrides:
        vals = overrides[ind_id]
        if isinstance(vals, list) and len(vals) == 48:
            return [_to_float(v) if v is not None else None for v in vals]
        if isinstance(vals, list) and vals:
            return vals
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
    """Return: 'date' | 'percent' | 'text' | 'number'"""
    formula = str(ind.get("formula_raw") or "")
    unit = ind.get("unit") or ""
    value = ind.get("value_year1")

    # Date: formula_raw contains datetime string
    if "00:00:00" in formula:
        return "date"
    # Percent: unit is %
    if unit == "%":
        return "percent"
    # Text: value is a non-numeric string
    if value is not None and isinstance(value, str) and _to_float(value) is None:
        return "text"
    return "number"


def _parse_date_value(val) -> date | None:
    """Parse a date value from various formats."""
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

def _render_indicator_row(ind: dict, depth: int = 0, hidden_set: set = None):
    """Render a single indicator with appropriate input widget and hide toggle."""
    if hidden_set is None:
        hidden_set = set()

    ind_id = ind["id"]
    name = ind.get("name", ind_id)
    unit = ind.get("unit") or ""
    seq = ind.get("section_number") or str(ind.get("row") or "")
    input_type = _detect_input_type(ind)
    current = get_current_value(ind)

    # Indent columns: [indent, seq, name, input, unit, hide_btn]
    # depth=0: no indent col; depth=1: 1 indent col
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
            # Store as ISO string for consistency
            new_val_store = new_val.strftime("%Y-%m-%d") if new_val else None

        elif input_type == "percent":
            float_val = _to_float(current) or 0.0
            # Clamp to [0, 1]
            float_val = max(0.0, min(1.0, float_val))
            pct_display = round(float_val * 100, 4)
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
            new_val_store = None  # read-only, no change

        else:  # number
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
        # Hide button (only for non-text types)
        if st.button("🙈", key=f"hide_{ind_id}", help="隐藏此指标"):
            _toggle_hidden(ind_id)

        # Apply button (only for editable types)
        if input_type != "text" and new_val_store is not None:
            if st.button("✓", key=f"apply_{ind_id}", help="应用修改"):
                st.session_state.param_changes[ind_id] = new_val_store
                st.rerun()

    # Show year series expander for multi-year number indicators
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
    """Render a collapsed/hidden indicator row with a show button."""
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


def _render_category(params: list, hidden_set: set):
    """Render all indicators in a category with tree-indented parent/child hierarchy."""
    # Separate roots and children
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
            _render_indicator_row(root, depth=0, hidden_set=hidden_set)

        # Render children (indented), regardless of root visibility
        for child in children_map.get(root_id, []):
            child_id = child["id"]
            if child_id in hidden_set:
                # Show hidden child with indent
                col_indent, col_rest = st.columns([0.5, 11.5])
                with col_rest:
                    _render_hidden_row(child)
            else:
                _render_indicator_row(child, depth=1, hidden_set=hidden_set)

    # Render any orphan children (parent not in this category's roots)
    root_ids = {r["id"] for r in roots}
    for p in params:
        pid = p.get("parent_id")
        if pid and pid not in root_ids:
            if p["id"] in hidden_set:
                _render_hidden_row(p)
            else:
                _render_indicator_row(p, depth=0, hidden_set=hidden_set)


# ── Action functions ────────────────────────────────────────────────────────────

def _do_save_and_recalculate():
    """Run recalculation in a background thread, update Neo4j on completion."""
    changes = st.session_state.param_changes
    if not changes:
        return

    # 1. Take snapshot on first save
    if not _has_snapshot():
        _take_snapshot()

    # 2. Save overrides immediately
    overrides = _load_overrides()
    for ind_id, new_val in changes.items():
        overrides[ind_id] = [new_val]
    _save_overrides(overrides)
    st.session_state.param_changes = {}

    # 3. Run recalculation in background
    st.info("⏳ 正在重算（约 10-30 秒）...")

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

            from src.graph.loader import GraphLoader
            with GraphLoader(
                config.NEO4J_URI,
                config.NEO4J_USER,
                config.NEO4J_PASSWORD,
                task_id=task_id,
            ) as loader:
                loader.update_indicator_values(new_values)

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

    import time
    for _ in range(60):
        time.sleep(1)
        if st.session_state.get("_recalc_success") or st.session_state.get("_recalc_error"):
            break
        msgs = st.session_state.get("_recalc_msgs", [])
        if msgs:
            st.info(f"⏳ {msgs[-1]}")
        st.rerun()

    if st.session_state.pop("_recalc_success", False):
        count = st.session_state.pop("_recalc_count", 0)
        st.success(f"✅ 重算完成，已更新 {count} 个指标的值！")
        st.session_state.pop("_recalc_msgs", None)
        st.cache_data.clear()
        st.rerun()
    elif st.session_state.get("_recalc_error"):
        err = st.session_state.pop("_recalc_error")
        st.error(f"❌ 重算失败: {err}")
        st.session_state.pop("_recalc_msgs", None)


def _take_snapshot():
    snapshot = {}
    for ind in indicators:
        vals = ind.get("values_json") or []
        snapshot[ind["id"]] = vals if vals else [ind.get("value_year1")]
    path = _snapshot_path()
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def _do_restore_original():
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
# PAGE RENDER
# ═══════════════════════════════════════════════════════════════════════════════

st.title(f"✏️ 修改参数值 — {meta.name}")

hidden_set = _load_hidden()
n_hidden = len(hidden_set)
st.caption(
    f"任务ID: {task_id[:8]}...  |  可编辑参数: {len(editable_params)} 个"
    + (f"  |  已隐藏: {n_hidden} 个" if n_hidden else "")
)

if st.button("← 返回任务详情"):
    st.switch_page("pages/2_task_detail.py")

st.divider()

if not editable_params:
    st.info("参数输入表中没有找到可编辑的参数（is_input=True 且无公式）。")
    st.stop()

# ── Snapshot banner ─────────────────────────────────────────────────────────────

if _has_snapshot() and not st.session_state.get("_snapshot_shown"):
    st.info("📸 已存在参数快照。点击「↩ 恢复原始值」可撤销所有修改。")
    st.session_state["_snapshot_shown"] = True

# ── Layout: left = editors, right = change log ─────────────────────────────────

col_editor, col_actions = st.columns([4, 1])

with col_editor:
    if len(categories) == 1:
        _render_category(groups[categories[0]], hidden_set)
    else:
        tabs = st.tabs(categories)
        for cat, tab in zip(categories, tabs):
            with tab:
                _render_category(groups[cat], hidden_set)


with col_actions:
    st.markdown("### 已修改")
    changes = st.session_state.param_changes

    if changes:
        for ind_id, new_val in changes.items():
            ind = next((i for i in editable_params if i["id"] == ind_id), None)
            if ind:
                old = get_current_value(ind)
                st.markdown(f"**{ind['name']}**\n`{old}` → `{new_val}`")
        st.divider()
    else:
        st.caption("尚未修改任何参数")

    col_save, col_restore = st.columns(2)

    with col_save:
        if st.button("💾 保存并重算", disabled=not changes, type="primary"):
            _do_save_and_recalculate()

    with col_restore:
        if st.button("↩ 恢复原始值", disabled=not _has_snapshot()):
            _do_restore_original()

    # Hidden indicators management
    if n_hidden:
        st.divider()
        st.markdown(f"### 已隐藏 ({n_hidden})")
        for ind_id in list(hidden_set):
            ind = next((i for i in editable_params if i["id"] == ind_id), None)
            if ind:
                col_name, col_show = st.columns([3, 1])
                with col_name:
                    st.caption(ind.get("name", ind_id))
                with col_show:
                    if st.button("👁", key=f"sidebar_show_{ind_id}", help="显示"):
                        _toggle_hidden(ind_id)

