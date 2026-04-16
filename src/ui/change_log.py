"""
change_log.py — 修改日志 Streamlit 组件

时间线列表格式，支持删除单条修改（恢复指标值）。
"""

from __future__ import annotations

from typing import Callable, Optional

import streamlit as st


def render_change_log(
    logs: list[dict],
    on_delete: Optional[Callable[[int, str], None]] = None,
    needs_recalc: bool = False,
):
    """
    渲染修改日志时间线。

    logs: TrialStore.get_change_logs() 返回的列表
          每条: {id, indicator_id, indicator_name, old_value, new_value, timestamp}
    on_delete: callable(log_id, indicator_id) — 删除回调
    needs_recalc: 是否显示"需要重新计算"提示
    """
    if not logs:
        st.caption("暂无修改记录")
        return

    if needs_recalc:
        st.warning("⚠️ 有修改已撤销，建议重新计算", icon="⚠️")

    st.caption(f"共 {len(logs)} 条修改")

    for log in reversed(logs):  # 最新的在最上面
        _render_log_entry(log, on_delete)


def _render_log_entry(log: dict, on_delete: Optional[Callable]):
    """渲染单条修改日志。"""
    log_id = log["id"]
    ind_id = log["indicator_id"]
    ind_name = log["indicator_name"]
    old_val = log.get("old_value")
    new_val = log.get("new_value")
    ts = log.get("timestamp", "")

    # 格式化时间（只显示时分秒）
    ts_display = ts[11:19] if len(ts) >= 19 else ts

    # 格式化数值
    def _fmt(v):
        if v is None:
            return "—"
        if isinstance(v, float):
            if abs(v) >= 1e6:
                return f"{v:,.0f}"
            if abs(v) < 0.01 and v != 0:
                return f"{v:.4g}"
            return f"{v:,.4g}"
        return str(v)

    old_str = _fmt(old_val)
    new_str = _fmt(new_val)

    col_time, col_info, col_btn = st.columns([1.2, 4, 0.8])

    with col_time:
        st.caption(ts_display)

    with col_info:
        st.markdown(
            f"**{ind_name}**  \n"
            f"<span style='color:#888'>{old_str}</span> → "
            f"<span style='color:#4CAF50;font-weight:bold'>{new_str}</span>",
            unsafe_allow_html=True,
        )

    with col_btn:
        if on_delete and st.button(
            "撤销",
            key=f"del_log_{log_id}",
            help=f"撤销对「{ind_name}」的修改，恢复为 {old_str}",
            type="secondary",
        ):
            on_delete(log_id, ind_id)

    st.divider()
