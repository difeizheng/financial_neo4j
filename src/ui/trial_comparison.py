"""
trial_comparison.py — 试算对比 Streamlit 组件

支持选择两个试算方案并排对比关键指标差异。
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.graph_objects as go


def render_trial_comparison(
    task_id: str,
    trial_store,
    task_manager,
):
    """渲染试算对比组件。"""
    trials = trial_store.get_trials(task_id)

    # 只显示已完成的试算
    done_trials = [t for t in trials if t["status"] == "done"]

    if len(done_trials) < 2:
        st.info("需要至少 2 个已完成的试算才能进行对比")
        return

    # 选择两个试算
    col1, col2 = st.columns(2)

    with col1:
        trial1_id = st.selectbox(
            "选择试算 A",
            options=[t["id"] for t in done_trials],
            format_func=lambda tid: next(
                (t["name"] for t in done_trials if t["id"] == tid), tid[:8]
            ),
            key="compare_trial_a",
        )

    with col2:
        trial2_id = st.selectbox(
            "选择试算 B",
            options=[t["id"] for t in done_trials],
            format_func=lambda tid: next(
                (t["name"] for t in done_trials if t["id"] == tid), tid[:8]
            ),
            key="compare_trial_b",
            index=1 if len(done_trials) > 1 else 0,
        )

    if not trial1_id or not trial2_id or trial1_id == trial2_id:
        st.warning("请选择两个不同的试算")
        return

    # 执行对比分析
    from src.trial.analyzer import TrialAnalyzer
    analyzer = TrialAnalyzer(trial_store, task_manager)

    comparison = analyzer.compare_trials(trial1_id, trial2_id, task_id)

    if "error" in comparison:
        st.error(comparison["error"])
        return

    # 显示对比结果
    st.divider()

    trial1_name = comparison["trial1"]["name"]
    trial2_name = comparison["trial2"]["name"]

    st.subheader(f"对比结果：{trial1_name} vs {trial2_name}")

    # 关键指标差异表格
    key_diffs = comparison.get("key_diffs", [])

    if key_diffs:
        st.markdown("### 关键指标差异")

        rows = []
        for d in key_diffs:
            rows.append({
                "指标名称": d.get("name", d["id"]),
                f"{trial1_name[:10]} 值": d.get("trial1_value"),
                f"{trial2_name[:10]} 值": d.get("trial2_value"),
                "差异": d.get("diff"),
                "单位": d.get("unit", ""),
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # 差异柱状图
        st.markdown("### 差异可视化")

        fig = go.Figure()

        names = [d.get("name", d["id"])[:20] for d in key_diffs[:10]]
        vals1 = [d.get("trial1_value") or 0 for d in key_diffs[:10]]
        vals2 = [d.get("trial2_value") or 0 for d in key_diffs[:10]]

        fig.add_trace(
            go.Bar(
                x=names,
                y=vals1,
                name=trial1_name[:15],
                marker_color="#3498db",
            )
        )
        fig.add_trace(
            go.Bar(
                x=names,
                y=vals2,
                name=trial2_name[:15],
                marker_color="#e74c3c",
            )
        )

        fig.update_layout(
            barmode="group",
            xaxis_title="指标",
            yaxis_title="值",
            height=350,
            margin=dict(l=20, r=20, t=20, b=60),
            legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        )

        st.plotly_chart(fig, use_container_width=True)

    else:
        st.info("关键指标无显著差异")


def render_trial_management(
    task_id: str,
    trial_store,
    trial_manager,
):
    """渲染试算管理组件（列表、切换、删除）。"""
    trials = trial_store.get_trials(task_id)

    if not trials:
        st.caption("暂无试算记录")
        return

    st.subheader("试算列表")

    for t in trials:
        trial_id = t["id"]
        status = t["status"]
        name = t["name"]
        note = t.get("note") or ""
        created = t.get("created_at", "")[:10] if t.get("created_at") else ""

        # 状态图标
        status_icon = {
            "done": "✅",
            "running": "⏳",
            "error": "❌",
            "pending": "📝",
        }.get(status, "❓")

        with st.container():
            col_status, col_name, col_date, col_actions = st.columns([0.5, 3, 1, 2])

            with col_status:
                st.markdown(f"### {status_icon}")

            with col_name:
                st.markdown(f"**{name}**")
                if note:
                    st.caption(f"备注: {note}")

            with col_date:
                st.caption(created)

            with col_actions:
                # 操作按钮
                btn_col1, btn_col2, btn_col3 = st.columns(3)

                with btn_col1:
                    if status == "done":
                        if st.button("查看", key=f"view_{trial_id}", type="primary"):
                            st.session_state._show_results = True
                            st.session_state._result_trial_id = trial_id
                            st.rerun()

                with btn_col2:
                    if st.button("删除", key=f"del_{trial_id}"):
                        trial_store.delete_trial(trial_id)
                        st.toast(f"已删除试算: {name}")
                        st.rerun()

                with btn_col3:
                    if status == "pending" and note:
                        if st.button("备注", key=f"note_{trial_id}"):
                            # 弹出编辑备注（简化版：直接显示）
                            st.caption(f"备注: {note}")

            st.divider()