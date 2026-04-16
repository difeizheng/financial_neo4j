"""
trial_results.py — 试算结果展示 Streamlit 组件

技术视角：变化指标列表、影响路径图、数据完整性验证
财务视角：IRR/NPV变化、敏感度排名、趋势对比图、热力图
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px


def render_trial_results(trial_id: str, task_id: str, trial_store, task_manager):
    """渲染试算结果（技术视角 + 财务视角 Tab）。"""
    from src.trial.analyzer import TrialAnalyzer

    analyzer = TrialAnalyzer(trial_store, task_manager)

    tab_tech, tab_fin = st.tabs(["技术视角", "财务视角"])

    with tab_tech:
        tech_data = analyzer.get_technical_view(trial_id, task_id)
        render_technical_view(tech_data)

    with tab_fin:
        fin_data = analyzer.get_financial_view(trial_id, task_id)
        render_financial_view(fin_data)


def render_technical_view(data: dict):
    """渲染技术视角（增强版：区分源头修改 vs 被动影响，展示依赖链）。"""
    if "error" in data:
        st.error(data["error"])
        return

    # 概览指标（增强）
    impact_stats = data.get("impact_stats", {})
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("重算指标总数", data.get("total_indicators", 0))
    col2.metric("源头修改数", impact_stats.get("source_count", 0), help="用户主动修改的参数")
    col3.metric("被动影响数", impact_stats.get("affected_count", 0), help="因依赖链变化的指标")
    col4.metric("最大影响深度", impact_stats.get("max_depth", 0), help="从源头到末端的最长路径")

    st.divider()

    # 影响路径图（新增）
    impact_edges = data.get("impact_edges", [])
    source_ids = data.get("source_ids_simplified", data.get("source_ids", []))  # 使用简化后的ID
    changed_indicators = data.get("changed_indicators", [])
    impact_by_depth = data.get("impact_by_depth", {})

    # 构建完整的节点信息列表（包含源头和所有被动影响）
    all_indicators = list(changed_indicators)  # 源头修改（包含值变化信息）

    # 简化 changed_indicators 中的ID格式
    for ind in all_indicators:
        if "__" in ind.get("id", ""):
            ind["id"] = ind["id"].split("__")[-1]

    # 添加所有被动影响节点（来自 impact_by_depth）
    for depth, indicators in impact_by_depth.items():
        for ind in indicators:
            # 检查是否已存在（避免重复）
            if ind["id"] not in [i["id"] for i in all_indicators]:
                all_indicators.append(ind)

    if impact_edges and source_ids:
        st.subheader("影响路径图")

        # 布局选择器
        from src.ui.impact_preview import (
            LAYOUT_HIERARCHICAL, LAYOUT_SHEET_GROUP, LAYOUT_FORCE, LAYOUT_RADIAL
        )

        layout_options = {
            "层级布局（推荐）": LAYOUT_HIERARCHICAL,
            "工作表分组": LAYOUT_SHEET_GROUP,
            "力导向布局": LAYOUT_FORCE,
            "圆形布局": LAYOUT_RADIAL,
        }

        layout_names = list(layout_options.keys())
        selected_layout_name = st.selectbox(
            "选择布局方式",
            layout_names,
            index=0,
            key="impact_graph_layout",
            help="层级布局：从左到右按深度排列，清晰展示影响传递方向\n"
                 "工作表分组：按工作表聚类，分析跨表依赖\n"
                 "力导向布局：自由浮动，探索整体结构\n"
                 "圆形布局：源头居中，向外扩散",
        )
        selected_layout = layout_options[selected_layout_name]

        # 层级颜色图例（仅在层级布局时显示）
        if selected_layout == LAYOUT_HIERARCHICAL:
            st.caption("**层级颜色说明：** 从左到右，层级 0（源头）→ 层级 1 → 层级 2 → ...")
            legend_cols = st.columns(9)
            legend_colors = [
                ("🔴 源头", "#e74c3c", "层级 0"),
                ("🟠 第1层", "#f39c12", "直接依赖源头"),
                ("🟡 第2层", "#f1c40f", "间接影响"),
                ("🟢 第3层", "#27ae60", ""),
                ("🔵 第4层", "#3498db", ""),
                ("🟣 第5层", "#9b59b6", ""),
                ("🔷 第6层", "#1abc9c", ""),
                ("🔶 第7层", "#e67e22", ""),
                ("⚫ 第8层+", "#2c3e50", "末端"),
            ]
            for col, (label, color, desc) in zip(legend_cols, legend_colors):
                col.markdown(
                    f"<div style='background:{color};color:#fff;padding:4px 8px;border-radius:3px;text-align:center;font-size:11px;'>{label}</div>",
                    unsafe_allow_html=True,
                )

        from src.ui.impact_preview import build_impact_graph_enhanced
        graph_html = build_impact_graph_enhanced(
            edges=impact_edges,
            source_ids=source_ids,
            changed_indicators=all_indicators,  # 使用完整的节点列表
            layout=selected_layout,
        )
        if graph_html:
            st.components.v1.html(graph_html, height=420, scrolling=True)
        else:
            st.info("无法生成影响路径图")
    else:
        st.info("无影响路径数据（可能是源头指标没有下游依赖）")

    st.divider()

    # 分层变化详情（新增）
    # 源头修改
    source_changes = data.get("source_changes", [])
    if source_changes:
        with st.expander("🔥 源头修改（用户主动修改）", expanded=True):
            rows = [{
                "指标名称": c.get("name", c["id"])[:30],
                "原始值": c.get("original"),
                "新值": c.get("new"),
                "变化幅度": f"{c.get('pct_change', 0):.2f}%",
                "工作表": c.get("sheet", ""),
            } for c in source_changes]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("未检测到源头修改")

    # 按深度展示被动影响
    impact_by_depth = data.get("impact_by_depth", {})
    total_depths = len(impact_by_depth)

    if impact_by_depth:
        st.subheader("分层影响详情")
        st.caption(f"共 {total_depths} 层影响链，按依赖距离分组")

        for depth in sorted(impact_by_depth.keys()):
            indicators = impact_by_depth[depth]
            # 过滤掉没有实际变化的指标（只保留有值变化的）
            changed_in_depth = [ind for ind in indicators if ind.get("pct_change") is not None]

            if changed_in_depth:
                # 前2层默认展开，其他层折叠
                expanded = depth <= 2

                with st.expander(f"第 {depth} 层影响（{len(changed_in_depth)} 个指标变化）", expanded=expanded):
                    rows = [{
                        "指标名称": c.get("name", c["id"])[:30],
                        "原始值": c.get("original"),
                        "新值": c.get("new"),
                        "变化幅度": f"{c.get('pct_change', 0):.2f}%",
                        "依赖来源": _find_dependency_source(c["id"], impact_edges),
                        "工作表": c.get("sheet", ""),
                    } for c in changed_in_depth]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            elif indicators:
                # 有潜在影响但实际没变化的指标
                with st.expander(f"第 {depth} 层潜在影响（{len(indicators)} 个指标，但值未变）", expanded=False):
                    rows = [{
                        "指标名称": c.get("name", c["id"])[:30],
                        "工作表": c.get("sheet", ""),
                        "状态": "依赖关系存在但计算值未变化",
                    } for c in indicators]
                    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # 数据完整性验证（保持不变）
    validation = data.get("validation", {})
    with st.expander("🔍 数据完整性验证", expanded=False):
        col1, col2 = st.columns(2)
        col1.metric("覆盖率", f"{validation.get('coverage_pct', 0):.1f}%")
        col2.metric("有第一年值", "✅" if validation.get("has_year1_values") else "❌")

        if validation.get("coverage_pct", 0) < 90:
            st.warning("覆盖率低于 90%，可能有指标未正确重算")
        else:
            st.success("数据完整性良好")


def _find_dependency_source(ind_id: str, edges: list[dict]) -> str:
    """查找指标的直接依赖来源（哪个指标的变化导致了它的变化）。"""
    # edges 中 source_id 是依赖者，target_id 是被依赖者
    # 我们需要找依赖 ind_id 的边，即 source_id == ind_id 的边
    # 然后返回 target_name（它的依赖来源）

    for edge in edges:
        if edge.get("source_id") == ind_id:
            target_name = edge.get("target_name", edge.get("target_id", ""))
            return target_name[:20] if len(target_name) > 20 else target_name
    return "-"


def render_financial_view(data: dict):
    """渲染财务视角。"""
    if "error" in data:
        st.error(data["error"])
        return

    # 1. 关键财务指标变化
    key_indicators = data.get("key_indicators", [])
    if key_indicators:
        st.subheader("关键财务指标变化")
        cols = st.columns(len(key_indicators[:4]))
        for i, (col, ind) in enumerate(zip(cols, key_indicators[:4])):
            with col:
                pct = ind.get("pct_change")
                pct_str = f"{pct:.2f}%" if pct else "N/A"
                col.metric(
                    ind["name"][:15],
                    value=ind.get("new", "N/A"),
                    delta=pct_str,
                    help=f"原始值: {ind.get('original')}",
                )

        # 详细列表
        with st.expander("查看所有关键指标"):
            rows = []
            for ind in key_indicators:
                rows.append({
                    "指标名称": ind.get("name", ind["id"]),
                    "原始值": ind.get("original"),
                    "新值": ind.get("new"),
                    "变化幅度": f"{ind.get('pct_change', 0):.2f}%" if ind.get("pct_change") else "N/A",
                    "单位": ind.get("unit", ""),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("未检测到关键财务指标变化（IRR/NPV等）")

    st.divider()

    # 2. 敏感度排名
    sensitivity = data.get("sensitivity_ranking", [])
    if sensitivity:
        st.subheader("敏感度排名")
        st.caption("哪些指标变化幅度最大（可能对财务结果影响最大）")

        # 柱状图
        top_n = min(10, len(sensitivity))
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=[s["name"][:20] for s in sensitivity[:top_n]],
                y=[abs(s["pct_change"]) for s in sensitivity[:top_n]],
                marker_color=["#e74c3c" if s["pct_change"] < 0 else "#27ae60" for s in sensitivity[:top_n]],
            )
        )
        fig.update_layout(
            xaxis_title="指标",
            yaxis_title="变化幅度 (%)",
            height=300,
            margin=dict(l=20, r=20, t=20, b=60),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # 3. 时间序列趋势对比
    time_series = data.get("time_series", {})
    if time_series and time_series.get("original") and time_series.get("new"):
        st.subheader(f"趋势对比图：{time_series.get('indicator_name', '未知')[:20]}")
        st.caption("48 年时间轴上的数值变化")

        orig = time_series.get("original", [])
        new = time_series.get("new", [])

        years = list(range(1, len(orig) + 1))

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=years,
                y=orig,
                name="原始值",
                line=dict(color="#888", width=2),
            )
        )
        fig.add_trace(
            go.Scatter(
                x=years,
                y=new,
                name="重算后",
                line=dict(color="#27ae60", width=2),
            )
        )
        fig.update_layout(
            xaxis_title="年份",
            yaxis_title="值",
            height=350,
            margin=dict(l=20, r=20, t=20, b=40),
            legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("无趋势对比数据")

    st.divider()

    # 4. 影响热力图
    heatmap = data.get("heatmap", {})
    if heatmap:
        st.subheader("工作表影响热力图")
        st.caption("按工作表分组的变化幅度分布")

        # 准备热力图数据
        sheets = list(heatmap.keys())
        avg_changes = [heatmap[s]["avg_change_pct"] for s in sheets]
        counts = [heatmap[s]["count"] for s in sheets]

        # 使用 plotly 表格/热力图
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                x=sheets,
                y=counts,
                marker=dict(
                    color=avg_changes,
                    colorbar=dict(title="平均变化 (%)"),
                    colorscale="Reds",
                ),
                text=[f"{c} 个" for c in counts],
                textposition="auto",
            )
        )
        fig.update_layout(
            xaxis_title="工作表",
            yaxis_title="变化指标数",
            height=300,
            margin=dict(l=20, r=20, t=20, b=60),
        )
        st.plotly_chart(fig, use_container_width=True)

        # 详细表格
        with st.expander("查看各工作表详情"):
            rows = []
            for sheet, d in heatmap.items():
                rows.append({
                    "工作表": sheet,
                    "变化指标数": d["count"],
                    "平均变化": f"{d['avg_change_pct']:.2f}%",
                    "最大变化": f"{d['max_change_pct']:.2f}%",
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("无热力图数据")