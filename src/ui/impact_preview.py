"""
impact_preview.py — 影响预览 Streamlit 组件

显示修改指标后的下游影响范围：
- 文本视图：受影响指标列表 + 统计
- 图形视图：pyvis 依赖路径图（复用 task_detail 的修复方案）
"""

from __future__ import annotations

import os
import re
from typing import Optional

import streamlit as st
import streamlit.components.v1 as components


def render_impact_preview(
    task_id: str,
    changed_indicator_ids: list[str],
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
):
    """
    渲染影响预览组件。

    changed_indicator_ids: 当前所有已修改的指标 ID 列表（不带 task_id 前缀）
    """
    if not changed_indicator_ids:
        st.caption("修改指标后，这里将显示影响范围")
        return

    from src.graph.impact_analyzer import ImpactAnalyzer

    # 收集所有修改指标的下游影响（合并去重）
    all_downstream: dict[str, dict] = {}  # {ind_id: {name, sheet, depth}}
    all_edges: list[dict] = []

    try:
        with ImpactAnalyzer(neo4j_uri, neo4j_user, neo4j_password, task_id=task_id) as analyzer:
            for ind_id in changed_indicator_ids:
                downstream = analyzer.get_downstream(ind_id, max_depth=6)
                for d in downstream:
                    existing = all_downstream.get(d["id"])
                    if existing is None or d["depth"] < existing["depth"]:
                        all_downstream[d["id"]] = d

                edges = analyzer.get_impact_edges(ind_id, max_depth=6)
                for e in edges:
                    key = (e["source_id"], e["target_id"])
                    if key not in {(x["source_id"], x["target_id"]) for x in all_edges}:
                        all_edges.append(e)

    except Exception as e:
        st.error(f"查询影响范围失败: {e}")
        return

    if not all_downstream:
        st.info("未找到下游影响指标（可能是叶节点或依赖关系未加载）")
        return

    # 统计信息
    total = len(all_downstream)
    max_depth = max(d["depth"] for d in all_downstream.values()) if all_downstream else 0
    sheets = sorted(set(d.get("sheet", "") for d in all_downstream.values() if d.get("sheet")))

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("受影响指标", total)
    col_b.metric("最大传递深度", max_depth)
    col_c.metric("涉及工作表", len(sheets))

    if sheets:
        st.caption("涉及工作表：" + "、".join(sheets))

    # 文本视图 / 图形视图 切换
    view_tab1, view_tab2 = st.tabs(["文本视图", "图形视图"])

    with view_tab1:
        _render_text_view(all_downstream)

    with view_tab2:
        _render_graph_view(
            task_id=task_id,
            changed_ids=changed_indicator_ids,
            downstream=all_downstream,
            edges=all_edges,
        )


def _render_text_view(downstream: dict[str, dict]):
    """按深度分组显示受影响指标列表。"""
    # 按深度分组
    by_depth: dict[int, list] = {}
    for d in downstream.values():
        depth = d["depth"]
        by_depth.setdefault(depth, []).append(d)

    for depth in sorted(by_depth.keys()):
        items = by_depth[depth]
        with st.expander(f"第 {depth} 层影响（{len(items)} 个指标）", expanded=(depth <= 2)):
            rows = []
            for item in sorted(items, key=lambda x: x.get("name", "")):
                rows.append({
                    "指标名称": item.get("name", item["id"]),
                    "工作表": item.get("sheet", ""),
                    "单位": item.get("unit", ""),
                })
            if rows:
                import pandas as pd
                st.dataframe(
                    pd.DataFrame(rows),
                    use_container_width=True,
                    hide_index=True,
                )


def _render_graph_view(
    task_id: str,
    changed_ids: list[str],
    downstream: dict[str, dict],
    edges: list[dict],
):
    """使用 pyvis 渲染影响路径图。"""
    # 限制节点数量，避免渲染卡顿
    MAX_NODES = 80
    if len(downstream) > MAX_NODES:
        st.warning(f"影响节点过多（{len(downstream)} 个），只显示前 {MAX_NODES} 个（按深度优先）")
        # 按深度排序，取前 MAX_NODES 个
        sorted_nodes = sorted(downstream.values(), key=lambda x: x["depth"])[:MAX_NODES]
        visible_ids = {n["id"] for n in sorted_nodes}
    else:
        visible_ids = set(downstream.keys())

    html = _build_impact_graph_html(
        task_id=task_id,
        changed_ids=changed_ids,
        downstream=downstream,
        edges=edges,
        visible_ids=visible_ids,
    )

    if html:
        components.html(html, height=520, scrolling=False)
    else:
        st.caption("无法生成图形视图")


def _build_impact_graph_html(
    task_id: str,
    changed_ids: list[str],
    downstream: dict[str, dict],
    edges: list[dict],
    visible_ids: set[str],
) -> Optional[str]:
    """构建 pyvis 影响路径图 HTML（含 Streamlit iframe 兼容修复）。"""
    try:
        from pyvis.network import Network
        import pyvis as _pyvis

        net = Network(
            height="480px",
            width="100%",
            directed=True,
            bgcolor="#1a1a2e",
        )
        net.set_options("""{
            "nodes": {"font": {"color": "#ffffff"}, "borderWidth": 2},
            "edges": {"color": {"color": "#aaaaaa"}, "arrows": {"to": {"enabled": true}}},
            "physics": {"enabled": false}
        }""")

        nodes_added: set = set()

        # 添加被修改的指标节点（红色）
        for ind_id in changed_ids:
            prefixed = f"{task_id}__{ind_id}" if task_id else ind_id
            label = ind_id[:20]
            # 尝试从 downstream 中找名称
            for d in downstream.values():
                pass  # downstream 是下游，不包含被修改的节点本身
            net.add_node(
                prefixed,
                label=label,
                color="#e74c3c",
                size=25,
                title=f"[已修改] {ind_id}",
            )
            nodes_added.add(prefixed)

        # 添加下游节点（按深度着色）
        depth_colors = {
            1: "#e8a838",
            2: "#27ae60",
            3: "#2980b9",
            4: "#8e44ad",
            5: "#16a085",
            6: "#d35400",
        }
        for ind_id, info in downstream.items():
            if ind_id not in visible_ids:
                continue
            depth = info.get("depth", 1)
            color = depth_colors.get(depth, "#555555")
            name = info.get("name", ind_id)
            label = name[:20] if len(name) > 20 else name
            tooltip = f"{name}\n工作表: {info.get('sheet', '')}\n深度: {depth}"
            net.add_node(ind_id, label=label, color=color, title=tooltip)
            nodes_added.add(ind_id)

        # 添加边
        edges_added: set = set()
        for edge in edges:
            src = edge["source_id"]
            tgt = edge["target_id"]
            if src in nodes_added and tgt in nodes_added:
                key = (src, tgt)
                if key not in edges_added:
                    edges_added.add(key)
                    net.add_edge(src, tgt)

        if len(nodes_added) < 2:
            return None

        html = net.generate_html()
        html = _fix_pyvis_html(html)
        return html

    except Exception as e:
        return None


def _fix_pyvis_html(html: str) -> str:
    """修复 pyvis HTML 在 Streamlit iframe 中的兼容性问题。"""
    import pyvis as _pyvis

    # 修复1: 内联 utils.js（避免 404）
    _utils_path = os.path.join(os.path.dirname(_pyvis.__file__), "lib", "bindings", "utils.js")
    if os.path.exists(_utils_path):
        with open(_utils_path, encoding="utf-8") as f:
            _utils_raw = f.read()
        _utils_safe = _utils_raw.replace("</script>", "<\\/script>")
        html = re.sub(
            r'<script\s+src="lib/bindings/utils\.js"></script>',
            "<script>" + _utils_safe + "</script>",
            html,
            count=1,
        )

    # 修复2: 删除不存在的 vis-network CSS
    html = re.sub(r"<link[^>]*vis-network[^>]*css[^>]*/?\s*>", "", html)

    # 修复3: 删除 node_modules 引用
    html = re.sub(r"<script[^>]*node_modules[^>]*>[\s]*</script>", "", html)
    html = re.sub(r"<link[^>]*node_modules[^>]*/?\s*>", "", html)

    return html


# ── 增强版影响路径图（用于试算结果展示）───────────────────────────────────────

# 布局类型常量
LAYOUT_HIERARCHICAL = "hierarchical"      # 层级布局：从左到右，源头在最左侧
LAYOUT_SHEET_GROUP = "sheet_group"         # 工作表分组：按工作表聚类
LAYOUT_FORCE = "force"                     # 力导向布局：自由浮动
LAYOUT_RADIAL = "radial"                   # 圆形布局：源头居中，向外扩散


def build_impact_graph_enhanced(
    edges: list[dict],
    source_ids: list[str],
    changed_indicators: list[dict],
    layout: str = LAYOUT_HIERARCHICAL,
) -> str:
    """
    构建增强的影响路径图，区分源头修改和被动影响。

    支持多种布局方式，用户可切换查看不同视角。

    Args:
        edges: [{source_id, target_id, source_name, target_name}]
            - source_id: 依赖者（被影响的指标）
            - target_id: 被依赖者（影响的来源）
        source_ids: 源头指标 ID 列表（用户主动修改的指标）
        changed_indicators: 所有变化指标 [{id, name, original, new, pct_change, sheet, is_source, formula_raw, is_input}]
        layout: 布局类型
            - "hierarchical": 层级布局（默认），从左到右按深度排列
            - "sheet_group": 工作表分组，按工作表聚类
            - "force": 力导向布局，自由浮动
            - "radial": 圆形布局，源头居中向外扩散

    Returns:
        HTML 字符串（pyvis 生成的图，已修复 Streamlit iframe 兼容性，含全屏按钮和布局切换）
    """
    try:
        from pyvis.network import Network

        net = Network(
            height="400px",
            width="100%",
            directed=True,
            bgcolor="#f8f9fa",
        )

        # 根据布局类型配置 physics 和选项
        if layout == LAYOUT_HIERARCHICAL:
            # 层级布局：从左到右，源头在左侧
            net.set_options("""{
                "nodes": {
                    "font": {"size": 12, "face": "Arial"},
                    "borderWidth": 2,
                    "shadow": true,
                    "borderWidthSelected": 4
                },
                "edges": {
                    "color": {"color": "#888", "opacity": 0.7},
                    "arrows": {"to": {"enabled": true, "scaleFactor": 0.8}},
                    "smooth": {"type": "curvedCW", "roundness": 0.2},
                    "font": {"size": 10, "align": "middle"},
                    "width": 1.5
                },
                "physics": {"enabled": false},
                "layout": {
                    "hierarchical": {
                        "enabled": true,
                        "direction": "LR",
                        "sortMethod": "directed",
                        "levelSeparation": 150,
                        "nodeSpacing": 100,
                        "treeSpacing": 200,
                        "blockShifting": true,
                        "edgeMinimization": true,
                        "parentCentralization": true
                    }
                },
                "interaction": {
                    "hover": true,
                    "tooltipDelay": 200,
                    "navigationButtons": true,
                    "keyboard": true
                }
            }""")

        elif layout == LAYOUT_SHEET_GROUP:
            # 工作表分组布局：使用力导向 + 节点分组
            net.set_options("""{
                "nodes": {
                    "font": {"size": 12, "face": "Arial"},
                    "borderWidth": 2,
                    "shadow": true,
                    "borderWidthSelected": 4
                },
                "edges": {
                    "color": {"color": "#888", "opacity": 0.6},
                    "arrows": {"to": {"enabled": true, "scaleFactor": 0.8}},
                    "smooth": {"type": "continuous"},
                    "width": 1.5
                },
                "physics": {
                    "enabled": true,
                    "forceAtlas2Based": {
                        "gravitationalConstant": -50,
                        "centralGravity": 0.01,
                        "springLength": 100,
                        "springConstant": 0.08,
                        "damping": 0.4
                    },
                    "minVelocity": 0.75,
                    "solver": "forceAtlas2Based"
                },
                "interaction": {
                    "hover": true,
                    "tooltipDelay": 200,
                    "navigationButtons": true,
                    "keyboard": true
                }
            }""")

        elif layout == LAYOUT_RADIAL:
            # 圆形布局：源头居中，下游向外扩散
            net.set_options("""{
                "nodes": {
                    "font": {"size": 12, "face": "Arial"},
                    "borderWidth": 2,
                    "shadow": true,
                    "borderWidthSelected": 4
                },
                "edges": {
                    "color": {"color": "#888", "opacity": 0.6},
                    "arrows": {"to": {"enabled": true, "scaleFactor": 0.8}},
                    "smooth": {"type": "continuous"},
                    "width": 1.5
                },
                "physics": {
                    "enabled": true,
                    "repulsion": {
                        "centralGravity": 0.2,
                        "springLength": 200,
                        "springConstant": 0.05,
                        "nodeDistance": 150,
                        "damping": 0.09
                    },
                    "minVelocity": 0.75,
                    "solver": "repulsion"
                },
                "interaction": {
                    "hover": true,
                    "tooltipDelay": 200,
                    "navigationButtons": true,
                    "keyboard": true
                }
            }""")

        else:
            # 力导向布局（默认 fallback）
            net.set_options("""{
                "nodes": {
                    "font": {"size": 12, "face": "Arial"},
                    "borderWidth": 2,
                    "shadow": true,
                    "borderWidthSelected": 4
                },
                "edges": {
                    "color": {"color": "#888", "opacity": 0.7},
                    "arrows": {"to": {"enabled": true, "scaleFactor": 0.8}},
                    "smooth": {"type": "continuous"},
                    "font": {"size": 10, "align": "middle"},
                    "width": 1.5
                },
                "physics": {
                    "enabled": true,
                    "hierarchicalRepulsion": {"centralGravity": 0.2},
                    "minVelocity": 0.75,
                    "solver": "hierarchicalRepulsion"
                },
                "interaction": {
                    "hover": true,
                    "tooltipDelay": 200,
                    "navigationButtons": true,
                    "keyboard": true
                }
            }""")

        # 收集所有节点
        all_nodes = set()
        for edge in edges:
            all_nodes.add(edge["source_id"])
            all_nodes.add(edge["target_id"])

        # 确保源头节点也在集合中（即使没有边）
        for sid in source_ids:
            all_nodes.add(sid)

        # 限制节点数量
        MAX_NODES = 100
        if len(all_nodes) > MAX_NODES:
            # 优先保留源头节点和深度较浅的节点
            priority_nodes = set(source_ids)
            for edge in edges:
                if edge["target_id"] in source_ids:
                    priority_nodes.add(edge["source_id"])
            # 补充其他节点直到上限
            remaining = sorted(all_nodes - priority_nodes)
            all_nodes = priority_nodes | set(remaining[:MAX_NODES - len(priority_nodes)])

        # 构建节点信息映射
        ind_info_map = {ind["id"]: ind for ind in changed_indicators}

        # 推断节点深度
        depth_map = _infer_all_depths(edges, source_ids)

        # 添加节点
        for node_id in all_nodes:
            is_source = node_id in source_ids

            # 获取节点深度（源头=0，第1层影响=1，...）
            depth = depth_map.get(node_id, 0)
            is_source = node_id in source_ids

            # 获取指标详情
            ind_info = ind_info_map.get(node_id)

            # 统一颜色方案：按层级深度着色
            # 层级 0（源头）= 红色，层级越大颜色越冷
            color = _depth_color(depth)

            # 源头节点特殊处理：更大尺寸，带火焰标签
            if is_source:
                size = 28
                label_prefix = "🔥 "
                type_label = "[源头修改]"
                depth_display = "源头 (层级 0)"
            else:
                size = max(14, 22 - depth * 2)
                label_prefix = ""
                type_label = f"[第 {depth} 层影响]"
                depth_display = f"层级 {depth}"

            # 层级颜色说明（用于 tooltip）
            depth_color_name = _depth_color_name(depth)

            # 构建富文本 tooltip（HTML 格式）
            if ind_info:
                node_name = ind_info.get("name", node_id)
                pct_change = ind_info.get("pct_change", 0) or 0
                original_val = ind_info.get("original", "N/A")
                new_val = ind_info.get("new", "N/A")
                sheet = ind_info.get("sheet", "")
                unit = ind_info.get("unit", "")
                formula_raw = ind_info.get("formula_raw", "")
                is_input = ind_info.get("is_input", False)

                # 格式化数值显示
                if isinstance(original_val, (int, float)):
                    original_str = f"{original_val:,.2f}" if abs(original_val) > 1000 else f"{original_val:.2f}"
                else:
                    original_str = str(original_val)

                if isinstance(new_val, (int, float)):
                    new_str = f"{new_val:,.2f}" if abs(new_val) > 1000 else f"{new_val:.2f}"
                else:
                    new_str = str(new_val)

                # 公式信息
                has_formula = formula_raw and formula_raw.strip() and formula_raw.strip().startswith("=")
                formula_display = formula_raw[:50] + "..." if formula_raw and len(formula_raw) > 50 else (formula_raw or "无公式")
                formula_type = "计算指标" if has_formula else "输入指标"

                # 构建 HTML tooltip（包含层级信息）
                tooltip = f"""<div style='padding:10px;font-size:13px;max-width:320px;'>
                    <b style='font-size:15px;color:{color};'>{node_name}</b><br/>
                    <hr style='margin:6px 0;border-color:#ddd;'/>
                    <span style='background:{color};color:#fff;padding:2px 8px;border-radius:3px;font-size:11px;'>{type_label}</span>
                    <span style='color:#666;font-size:11px;margin-left:4px;'>{depth_color_name}</span><br/>
                    <b style='margin-top:4px;'>层级:</b> {depth_display}<br/>
                    <b>类型:</b> {formula_type} {('(输入参数)' if is_input else '')}<br/>
                    <b>工作表:</b> {sheet}<br/>
                    <hr style='margin:6px 0;border-color:#ddd;'/>
                    <b>原始值:</b> <span style='color:#888;'>{original_str}</span> {unit}<br/>
                    <b>新值:</b> <span style='color:#333;font-weight:bold;'>{new_str}</span> {unit}<br/>
                    <b>变化:</b> <span style='color:{'#e74c3c' if pct_change < 0 else '#27ae60'};'>{pct_change:+.2f}%</span><br/>
                    <hr style='margin:6px 0;border-color:#ddd;'/>
                    <b>公式:</b> <span style='font-size:11px;color:#555;background:#f5f5f5;padding:2px 4px;border-radius:2px;'>{formula_display}</span>
                </div>"""
            else:
                # 从边中找名称
                node_name = node_id
                for edge in edges:
                    if edge["source_id"] == node_id:
                        node_name = edge.get("source_name", node_id)
                        break
                    if edge["target_id"] == node_id:
                        node_name = edge.get("target_name", node_id)
                        break
                tooltip = f"""<div style='padding:8px;font-size:13px;'>
                    <b>{node_name}</b><br/>
                    <span style='color:#666;'>（无详细数据）</span>
                </div>"""

            label = f"{label_prefix}{node_name[:18]}"

            # 根据布局类型添加额外的节点属性
            # 注意：pyvis add_node 的第一个参数 n_id 必须是位置参数
            if layout == LAYOUT_HIERARCHICAL:
                # 层级布局：添加 level 属性，源头为 level 0，下游递增
                depth = depth_map.get(node_id, 0)
                net.add_node(
                    node_id,
                    label=label,
                    title=tooltip,
                    color=color,
                    size=size,
                    level=depth,
                )

            elif layout == LAYOUT_SHEET_GROUP:
                # 工作表分组：添加 group 属性（工作表名称）
                sheet_name = ""
                if ind_info:
                    sheet_name = ind_info.get("sheet", "未知")
                else:
                    # 从边中尝试获取
                    for edge in edges:
                        if edge["source_id"] == node_id:
                            sheet_name = edge.get("source_name", node_id).split("_")[0] if "_" in edge.get("source_name", node_id) else "未知"
                            break
                net.add_node(
                    node_id,
                    label=label,
                    title=tooltip,
                    color=color,
                    size=size,
                    group=sheet_name or "未知",
                )

            elif layout == LAYOUT_RADIAL:
                # 圆形布局：源头居中，下游按深度向外扩散（通过 level 控制）
                depth = depth_map.get(node_id, 0)
                net.add_node(
                    node_id,
                    label=label,
                    title=tooltip,
                    color=color,
                    size=size,
                    level=depth,
                )

            else:
                # 力导向布局（默认）
                net.add_node(
                    node_id,
                    label=label,
                    title=tooltip,
                    color=color,
                    size=size,
                )

        # 添加边（带详细关联信息）
        for edge in edges:
            src = edge["source_id"]  # 依赖者
            tgt = edge["target_id"]  # 被依赖者

            if src in all_nodes and tgt in all_nodes:
                src_name = edge.get("source_name", src)[:20]
                tgt_name = edge.get("target_name", tgt)[:20]

                # 边的 tooltip（HTML 格式）
                edge_tooltip = f"""<div style='padding:6px;font-size:12px;'>
                    <b>依赖关系</b><br/>
                    <span style='color:#3498db;'>{src_name}</span><br/>
                    <span style='color:#888;'>依赖于</span><br/>
                    <span style='color:#e74c3c;'>{tgt_name}</span><br/>
                    <hr style='margin:4px 0;border-color:#ddd;'/>
                    <i style='color:#666;'>修改 {tgt_name} 会影响 {src_name}</i>
                </div>"""

                net.add_edge(
                    tgt,  # 被依赖者（源头方向）
                    src,  # 依赖者（被影响方向）
                    title=edge_tooltip,
                    color="#888",
                    label="→",  # 简短标签
                )

        # 生成 HTML
        html = net.generate_html()
        html = _fix_pyvis_html(html)

        # 构建节点数据注册脚本（用于点击显示详情）
        node_data_script = _build_node_data_script(ind_info_map, source_ids, depth_map)

        # 添加全屏按钮、节点详情面板和自定义样式
        html = _add_fullscreen_button(html)

        # 注入节点数据
        if "</body>" in html:
            html = html.replace("</body>", node_data_script + "</body>")
        else:
            html += node_data_script

        return html

    except Exception as e:
        import logging
        logging.exception(f"生成影响路径图失败: {e}")
        return ""


def _add_fullscreen_button(html: str) -> str:
    """
    在生成的 HTML 中添加全屏按钮、节点详情面板和交互事件。

    功能：
    1. 全屏按钮 - 点击后图形进入全屏模式
    2. 节点详情面板 - 点击节点后显示详细信息卡片
    3. 节点选中事件 - 高亮选中节点及其关联边
    """
    # 样式：全屏按钮 + 详情面板 + 节点高亮
    custom_css = """
    <style>
        /* 全屏按钮 */
        .fullscreen-btn {
            position: absolute;
            top: 10px;
            right: 10px;
            z-index: 1000;
            background: rgba(255,255,255,0.95);
            border: 1px solid #ccc;
            border-radius: 4px;
            padding: 6px 12px;
            cursor: pointer;
            font-size: 12px;
            font-family: Arial, sans-serif;
            box-shadow: 0 2px 6px rgba(0,0,0,0.15);
            transition: all 0.2s;
        }
        .fullscreen-btn:hover {
            background: #fff;
            border-color: #999;
            box-shadow: 0 3px 8px rgba(0,0,0,0.2);
        }

        /* 节点详情面板 */
        .node-detail-panel {
            position: absolute;
            bottom: 10px;
            left: 10px;
            right: 10px;
            z-index: 999;
            background: rgba(255,255,255,0.98);
            border: 1px solid #ddd;
            border-radius: 8px;
            padding: 12px 16px;
            box-shadow: 0 3px 10px rgba(0,0,0,0.15);
            font-family: Arial, sans-serif;
            display: none;
            max-height: 180px;
            overflow-y: auto;
        }
        .node-detail-panel.visible {
            display: block;
        }
        .node-detail-panel .panel-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 8px;
            padding-bottom: 8px;
            border-bottom: 1px solid #eee;
        }
        .node-detail-panel .panel-header h4 {
            margin: 0;
            font-size: 14px;
            color: #333;
        }
        .node-detail-panel .close-btn {
            background: none;
            border: none;
            cursor: pointer;
            font-size: 18px;
            color: #888;
            padding: 2px 6px;
        }
        .node-detail-panel .close-btn:hover {
            color: #e74c3c;
        }
        .node-detail-panel .info-row {
            display: flex;
            margin: 4px 0;
            font-size: 13px;
        }
        .node-detail-panel .info-label {
            width: 80px;
            color: #666;
            font-weight: 500;
        }
        .node-detail-panel .info-value {
            flex: 1;
            color: #333;
        }
        .node-detail-panel .change-positive {
            color: #27ae60;
        }
        .node-detail-panel .change-negative {
            color: #e74c3c;
        }
        .node-detail-panel .formula-box {
            background: #f5f5f5;
            padding: 6px 10px;
            border-radius: 4px;
            font-size: 11px;
            color: #555;
            margin-top: 6px;
            word-break: break-all;
        }
        .node-detail-panel .tag-source {
            background: #e74c3c;
            color: #fff;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 11px;
        }
        .node-detail-panel .tag-affected {
            background: #27ae60;
            color: #fff;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 11px;
        }

        /* 全屏模式下的样式 */
        #mynetwork:-webkit-full-screen,
        #mynetwork:-moz-full-screen,
        #mynetwork:fullscreen {
            width: 100% !important;
            height: 100% !important;
        }
        #mynetwork:-webkit-full-screen .node-detail-panel,
        #mynetwork:-moz-full-screen .node-detail-panel,
        #mynetwork:fullscreen .node-detail-panel {
            max-height: 250px;
        }
    </style>
    """

    # JavaScript：节点数据 + 事件处理 + 全屏功能
    custom_js = """
    <script>
        // 节点数据存储（用于详情显示）
        var nodeDataStore = {};

        // 注册节点数据（由 Python 调用）
        function registerNodeData(nodeId, data) {
            nodeDataStore[nodeId] = data;
        }

        // 初始化函数
        function initGraphInteractions() {
            var networkDiv = document.getElementById('mynetwork');
            if (!networkDiv) return;

            // 创建详情面板
            var detailPanel = document.createElement('div');
            detailPanel.className = 'node-detail-panel';
            detailPanel.id = 'nodeDetailPanel';
            detailPanel.innerHTML = `
                <div class="panel-header">
                    <h4 id="panelTitle">节点详情</h4>
                    <button class="close-btn" onclick="hideDetailPanel()">×</button>
                </div>
                <div id="panelContent"></div>
            `;
            networkDiv.parentElement.appendChild(detailPanel);

            // 创建全屏按钮
            var fullscreenBtn = document.createElement('button');
            fullscreenBtn.className = 'fullscreen-btn';
            fullscreenBtn.innerHTML = '⛶ 全屏';
            fullscreenBtn.onclick = function() { toggleFullscreen(networkDiv); };
            networkDiv.parentElement.style.position = 'relative';
            networkDiv.parentElement.insertBefore(fullscreenBtn, networkDiv);

            // 监听 vis.js 网络的点击事件
            if (window.visNetwork) {
                window.visNetwork.on('click', function(params) {
                    if (params.nodes && params.nodes.length > 0) {
                        var nodeId = params.nodes[0];
                        showNodeDetail(nodeId);
                    } else {
                        hideDetailPanel();
                    }
                });
            }
        }

        // 全屏切换
        function toggleFullscreen(elem) {
            if (!document.fullscreenElement && !document.mozFullScreenElement &&
                !document.webkitFullscreenElement && !document.msFullscreenElement) {
                if (elem.requestFullscreen) elem.requestFullscreen();
                else if (elem.msRequestFullscreen) elem.msRequestFullscreen();
                else if (elem.mozRequestFullScreen) elem.mozRequestFullScreen();
                else if (elem.webkitRequestFullscreen) elem.webkitRequestFullscreen(Element.ALLOW_KEYBOARD_INPUT);
            } else {
                if (document.exitFullscreen) document.exitFullscreen();
                else if (document.msExitFullscreen) document.msExitFullscreen();
                else if (document.mozCancelFullScreen) document.mozCancelFullScreen();
                else if (document.webkitExitFullscreen) document.webkitExitFullscreen();
            }
        }

        // 显示详情面板
        function showNodeDetail(nodeId) {
            var data = nodeDataStore[nodeId];
            var panel = document.getElementById('nodeDetailPanel');
            var titleEl = document.getElementById('panelTitle');
            var contentEl = document.getElementById('panelContent');

            if (data && data.detailHtml) {
                titleEl.textContent = data.name || nodeId;
                contentEl.innerHTML = data.detailHtml;
                panel.classList.add('visible');
            } else {
                // 尝试从 tooltip 解析
                var network = window.visNetwork;
                if (network) {
                    var node = network.body.data.nodes.get(nodeId);
                    if (node && node.title) {
                        titleEl.textContent = node.label || nodeId;
                        contentEl.innerHTML = node.title;
                        panel.classList.add('visible');
                    } else {
                        hideDetailPanel();
                    }
                } else {
                    hideDetailPanel();
                }
            }
        }

        // 隐藏详情面板
        function hideDetailPanel() {
            var panel = document.getElementById('nodeDetailPanel');
            if (panel) panel.classList.remove('visible');
        }

        // 延迟初始化（等待 vis.js 加载完成）
        setTimeout(initGraphInteractions, 300);
    </script>
    """

    # 在 </body> 前插入
    if "</body>" in html:
        html = html.replace("</body>", custom_css + custom_js + "</body>")
    else:
        html += custom_css + custom_js

    return html


def _build_node_detail_html(ind_info: dict, is_source: bool, depth: int = 0) -> str:
    """
    构建节点详情面板的 HTML 内容（包含层级信息）。

    Args:
        ind_info: 指标详细信息字典
        is_source: 是否为源头修改
        depth: 影响深度（层级）

    Returns:
        HTML 字符串
    """
    if not ind_info:
        return """<div class="info-row"><span class="info-label">状态:</span><span class="info-value">暂无详细数据</span></div>"""

    name = ind_info.get("name", "未知")
    pct_change = ind_info.get("pct_change", 0) or 0
    original_val = ind_info.get("original", "N/A")
    new_val = ind_info.get("new", "N/A")
    sheet = ind_info.get("sheet", "未知")
    unit = ind_info.get("unit", "")
    formula_raw = ind_info.get("formula_raw", "")
    is_input = ind_info.get("is_input", False)

    # 格式化数值
    if isinstance(original_val, (int, float)):
        original_str = f"{original_val:,.4g}" if abs(original_val) >= 1000 else f"{original_val:.4g}"
    else:
        original_str = str(original_val) if original_val else "N/A"

    if isinstance(new_val, (int, float)):
        new_str = f"{new_val:,.4g}" if abs(new_val) >= 1000 else f"{new_val:.4g}"
    else:
        new_str = str(new_val) if new_val else "N/A"

    # 变化百分比颜色
    change_class = "change-positive" if pct_change >= 0 else "change-negative"
    change_sign = "+" if pct_change >= 0 else ""

    # 层级颜色和标签
    color = _depth_color(depth)
    color_name = _depth_color_name(depth)
    type_tag = "<span class='tag-source'>源头修改</span>" if is_source else f"<span class='tag-affected'>第{depth}层影响</span>"
    input_type = "输入参数" if is_input else "计算指标"
    depth_display = "源头 (层级 0)" if is_source else f"层级 {depth}"

    # 公式显示
    formula_display = formula_raw if formula_raw else "(无公式)"

    detail_html = f"""
        <div class="info-row">
            <span class="info-label">层级:</span>
            <span class="info-value"><span style='background:{color};color:#fff;padding:1px 6px;border-radius:2px;'>{depth_display}</span> {color_name}</span>
        </div>
        <div class="info-row">
            <span class="info-label">类型:</span>
            <span class="info-value">{type_tag} {input_type}</span>
        </div>
        <div class="info-row">
            <span class="info-label">工作表:</span>
            <span class="info-value">{sheet}</span>
        </div>
        <hr style='margin:8px 0;border-color:#eee;'/>
        <div class="info-row">
            <span class="info-label">原始值:</span>
            <span class="info-value">{original_str} {unit}</span>
        </div>
        <div class="info-row">
            <span class="info-label">新值:</span>
            <span class="info-value">{new_str} {unit}</span>
        </div>
        <div class="info-row">
            <span class="info-label">变化:</span>
            <span class="info-value {change_class}">{change_sign}{pct_change:.2f}%</span>
        </div>
        <hr style='margin:8px 0;border-color:#eee;'/>
        <div class="info-row">
            <span class="info-label">公式:</span>
        </div>
        <div class="formula-box">{formula_display}</div>
    """

    return detail_html


def _build_node_data_script(
    ind_info_map: dict[str, dict],
    source_ids: list[str],
    depth_map: dict[str, int],
) -> str:
    """
    构建节点数据注册脚本，用于点击节点时显示详细信息。

    Args:
        ind_info_map: 节点 ID -> 指标信息的映射
        source_ids: 源头指标 ID 列表
        depth_map: 节点 ID -> 深度的映射

    Returns:
        JavaScript 脚本字符串
    """
    script_lines = ["<script>"]

    for node_id, ind_info in ind_info_map.items():
        is_source = node_id in source_ids
        depth = depth_map.get(node_id, 0) if not is_source else 0

        # 构建详情 HTML
        detail_html = _build_node_detail_html(ind_info, is_source, depth)
        name = ind_info.get("name", node_id)

        # 转义 HTML 中的特殊字符
        detail_html_escaped = detail_html.replace("'", "\\'").replace("\n", "")

        script_lines.append(
            f"registerNodeData('{node_id}', {{name: '{name}', detailHtml: '{detail_html_escaped}', isSource: {str(is_source).lower()}}});"
        )

    script_lines.append("</script>")

    return "\n".join(script_lines)


def _infer_all_depths(edges: list[dict], source_ids: list[str]) -> dict[str, int]:
    """
    从边关系推断所有节点的深度。

    使用 BFS 从源头节点出发，计算每个节点的最短路径深度。

    Args:
        edges: [{source_id, target_id}]
            - source_id: 依赖者（依赖 target_id）
            - target_id: 被依赖者
        source_ids: 源头指标 ID 列表

    Returns:
        {node_id: depth} 深度映射，源头节点深度为 0
    """
    # 构建反向邻接表：从 target_id -> source_id
    # 因为 edge 表示 source 依赖 target，修改 target 会影响 source
    # 所以从源头（target）出发，找所有依赖它的 source
    reverse_adj: dict[str, list[str]] = {}

    for edge in edges:
        tgt = edge["target_id"]
        src = edge["source_id"]
        if tgt not in reverse_adj:
            reverse_adj[tgt] = []
        reverse_adj[tgt].append(src)

    # BFS 计算深度
    visited = {sid: 0 for sid in source_ids}
    queue = list(source_ids)

    while queue:
        current = queue.pop(0)
        current_depth = visited[current]

        # 找所有依赖 current 的节点（它们会被影响）
        neighbors = reverse_adj.get(current, [])
        for neighbor in neighbors:
            if neighbor not in visited:
                visited[neighbor] = current_depth + 1
                queue.append(neighbor)
            else:
                # 如果已经访问过，更新为更短的路径
                visited[neighbor] = min(visited[neighbor], current_depth + 1)

    return visited


def _depth_color_name(depth: int) -> str:
    """
    根据层级返回颜色名称（用于 tooltip 显示）。

    Args:
        depth: 节点的层级深度

    Returns:
        颜色名称字符串
    """
    names = {
        0: "红色（源头）",
        1: "橙色（第1层）",
        2: "黄色（第2层）",
        3: "绿色（第3层）",
        4: "蓝色（第4层）",
        5: "紫色（第5层）",
        6: "青色（第6层）",
        7: "深橙色（第7层）",
        8: "深蓝灰（第8层+）",
    }
    return names.get(min(depth, 8), "灰色（末端）")


def _depth_color(depth: int) -> str:
    """
    根据深度/层级返回颜色（渐变）。

    层级 0 = 源头（红色）
    层级越小（越靠近源头），颜色越暖（红→橙→黄→绿→蓝→紫）
    层级越大（越远离源头），颜色越冷

    Args:
        depth: 节点的层级深度（0=源头，1=第1层影响，...）

    Returns:
        颜色十六进制值
    """
    # 统一的层级颜色方案：从源头（层级0）到末端逐层渐变
    colors = {
        0: "#e74c3c",  # 红色 - 源头修改（用户主动修改）
        1: "#f39c12",  # 橙色 - 第1层影响（直接依赖源头）
        2: "#f1c40f",  # 黄色 - 第2层影响
        3: "#27ae60",  # 绿色 - 第3层影响
        4: "#3498db",  # 蓝色 - 第4层影响
        5: "#9b59b6",  # 紫色 - 第5层影响
        6: "#1abc9c",  # 青色 - 第6层影响
        7: "#e67e22",  # 深橙色 - 第7层+
        8: "#2c3e50",  # 深蓝灰 - 第8层+
    }
    return colors.get(min(depth, 8), "#95a5a6")  # 默认浅灰色
