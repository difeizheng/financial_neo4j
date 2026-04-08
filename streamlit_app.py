"""
streamlit_app.py

抽水蓄能财务知识图谱 — Streamlit 全流程可视化界面
Excel上传 → 解析 → Neo4j加载 → 验证 → LLM对话查询
"""

import json
import os
import tempfile
import time
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

import config
from src.parser.indicator_registry import (
    extract_indicators,
    load_indicators,
    save_indicators,
)
from src.parser.formula_parser import (
    parse_dependencies,
    load_dependencies,
    save_dependencies,
)
from src.parser.value_extractor import extract_values
from src.parser.sheet_config import SHEET_CATEGORIES
from src.graph.loader import GraphLoader
from src.graph.validator import GraphValidator
from src.graph.schema import SHEET_DESCRIPTIONS, SHEET_FEED_INTO
from src.llm.cypher_generator import FinancialGraphChat

st.set_page_config(
    page_title="抽水蓄能财务知识图谱",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── 报表类别颜色映射 ──────────────────────────────────────────────────────────
SHEET_COLORS = {
    "参数输入": "#FF6B6B",
    "辅助计算": "#4ECDC4",
    "投资估算": "#45B7D1",
    "融资与债务": "#96CEB4",
    "折旧摊销": "#FFEAA7",
    "成本费用": "#DDA0DD",
    "收入与税金": "#98D8C8",
    "利润表": "#F7DC6F",
    "现金流量表": "#82E0AA",
    "资产负债表": "#AED6F1",
    "其他": "#AAAAAA",
}


# ── Session State 初始化 ──────────────────────────────────────────────────────
def init_session_state():
    defaults = {
        "pipeline_status": "idle",
        "indicators": None,
        "edges": None,
        "validation_results": None,
        "uploaded_file_name": None,
        "_uploaded_excel_path": None,
        "graph_view_mode": "sheet_overview",
        "selected_sheet": "全部",
        "search_indicator": "",
        "hop_depth": 1,
        "chat_obj": None,
        "chat_messages": [],
        "neo4j_connected": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


# ── Neo4j 连接测试 ────────────────────────────────────────────────────────────
def test_neo4j_connection() -> bool:
    try:
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
        )
        with driver.session() as session:
            session.run("RETURN 1")
        driver.close()
        return True
    except Exception:
        return False


# ── 图可视化构建 ──────────────────────────────────────────────────────────────
def _get_pyvis_html(net) -> str:
    """从 pyvis Network 对象获取 HTML 字符串（兼容不同版本）。"""
    try:
        return net.generate_html()
    except AttributeError:
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".html", mode="w", encoding="utf-8"
        )
        tmp.close()
        net.save_graph(tmp.name)
        html = Path(tmp.name).read_text(encoding="utf-8")
        os.unlink(tmp.name)
        return html


def build_sheet_overview_graph(indicators: list, edges: list) -> str:
    """报表总览：14个Sheet节点 + FEEDS_INTO边。"""
    from pyvis.network import Network

    net = Network(
        height="500px", width="100%", directed=True,
        bgcolor="#0E1117", font_color="white",
    )
    net.barnes_hut(gravity=-3000, central_gravity=0.3, spring_length=200)

    sheet_counts: dict[str, int] = {}
    for ind in indicators:
        sheet_counts[ind["sheet"]] = sheet_counts.get(ind["sheet"], 0) + 1

    for sheet_name, count in sheet_counts.items():
        cat = SHEET_CATEGORIES.get(sheet_name, "其他")
        color = SHEET_COLORS.get(cat, SHEET_COLORS["其他"])
        desc = SHEET_DESCRIPTIONS.get(sheet_name, "")
        net.add_node(
            sheet_name,
            label=f"{sheet_name}\n({count})",
            title=f"{sheet_name}\n{desc}\n指标数: {count}",
            color=color,
            size=max(20, 15 + count // 8),
            font={"size": 13},
        )

    added_sheets = set(sheet_counts.keys())
    for src, tgt in SHEET_FEED_INTO:
        if src in added_sheets and tgt in added_sheets:
            net.add_edge(src, tgt, color="#666666", arrows="to", width=1.5)

    return _get_pyvis_html(net)


def build_indicator_detail_graph(
    indicators: list,
    edges: list,
    filter_sheet: str | None = None,
    search_name: str | None = None,
    hop_depth: int = 1,
) -> str:
    """指标详情：按报表/搜索过滤，展开N跳邻域。"""
    from pyvis.network import Network

    ind_by_id = {ind["id"]: ind for ind in indicators}
    MAX_NODES = 500

    # 确定初始节点集
    if search_name:
        seed_ids = {ind["id"] for ind in indicators if search_name in ind["name"]}
        included_ids = set(seed_ids)
        frontier = set(seed_ids)
        for _ in range(hop_depth):
            next_frontier: set[str] = set()
            for edge in edges:
                if edge["source_id"] in frontier:
                    next_frontier.add(edge["target_id"])
                if edge["target_id"] in frontier:
                    next_frontier.add(edge["source_id"])
            new_nodes = next_frontier - included_ids
            included_ids |= new_nodes
            frontier = new_nodes
    elif filter_sheet and filter_sheet != "全部":
        included_ids = {ind["id"] for ind in indicators if ind["sheet"] == filter_sheet}
        for edge in edges:
            if edge["source_id"] in included_ids:
                included_ids.add(edge["target_id"])
            if edge["target_id"] in included_ids:
                included_ids.add(edge["source_id"])
    else:
        connected: set[str] = set()
        for edge in edges:
            connected.add(edge["source_id"])
            connected.add(edge["target_id"])
        included_ids = connected

    # 节点数量上限
    if len(included_ids) > MAX_NODES:
        included_ids = set(list(included_ids)[:MAX_NODES])

    net = Network(
        height="500px", width="100%", directed=True,
        bgcolor="#0E1117", font_color="white",
    )
    net.barnes_hut(gravity=-2000, central_gravity=0.3, spring_length=150)

    seed_ids_set = (
        {ind["id"] for ind in indicators if search_name in ind["name"]}
        if search_name else set()
    )

    for nid in included_ids:
        ind = ind_by_id.get(nid)
        if not ind:
            continue
        cat = SHEET_CATEGORIES.get(ind["sheet"], "其他")
        color = SHEET_COLORS.get(cat, SHEET_COLORS["其他"])
        is_seed = nid in seed_ids_set
        formula_preview = (ind.get("formula_raw") or "N/A")[:80]
        net.add_node(
            nid,
            label=ind["name"][:14],
            title=(
                f"{ind['name']}\n"
                f"报表: {ind['sheet']}\n"
                f"公式: {formula_preview}\n"
                f"值(年1): {ind.get('value_year1', 'N/A')}"
            ),
            color="#FF4444" if is_seed else color,
            size=22 if is_seed else 10,
            font={"size": 10},
        )

    for edge in edges:
        if edge["source_id"] in included_ids and edge["target_id"] in included_ids:
            if edge.get("is_circular"):
                edge_color = "#FF6666"
            elif edge.get("is_cross_sheet"):
                edge_color = "#FFAA00"
            else:
                edge_color = "#555555"
            net.add_edge(
                edge["source_id"],
                edge["target_id"],
                color=edge_color,
                title=f"{edge.get('operation', '')} | {edge.get('formula_fragment', '')}",
                arrows="to",
                width=1,
            )

    return _get_pyvis_html(net)


# ── 侧边栏 ────────────────────────────────────────────────────────────────────
def render_sidebar():
    with st.sidebar:
        st.title("⚡ 抽水蓄能财务知识图谱")
        st.caption("Excel → Neo4j → LLM 全流程可视化")
        st.divider()

        # ── 数据导入 ──
        st.subheader("📥 数据导入")

        uploaded_file = st.file_uploader(
            "上传Excel财务模型",
            type=["xlsx"],
            help="支持抽水蓄能财务模型Excel文件（.xlsx）",
        )

        excel_path: Path | None = None
        if uploaded_file:
            if st.session_state.uploaded_file_name != uploaded_file.name:
                tmp_path = Path(tempfile.gettempdir()) / uploaded_file.name
                tmp_path.write_bytes(uploaded_file.getvalue())
                st.session_state.uploaded_file_name = uploaded_file.name
                st.session_state._uploaded_excel_path = tmp_path
            excel_path = st.session_state._uploaded_excel_path
        elif config.EXCEL_FILE.exists():
            st.info(f"默认文件: {config.EXCEL_FILE.name[:30]}...")
            excel_path = config.EXCEL_FILE

        col1, col2 = st.columns(2)
        with col1:
            run_btn = st.button("🚀 一键执行", use_container_width=True, type="primary",
                                disabled=(excel_path is None))
        with col2:
            load_btn = st.button("📂 加载已有", use_container_width=True,
                                 disabled=not (config.INDICATORS_FILE.exists()
                                               and config.DEPENDENCIES_FILE.exists()))

        if load_btn:
            with st.spinner("加载已有数据..."):
                st.session_state.indicators = load_indicators(config.INDICATORS_FILE)
                st.session_state.edges = load_dependencies(config.DEPENDENCIES_FILE)
                st.session_state.pipeline_status = "done"
            st.success(
                f"已加载: {len(st.session_state.indicators)}个指标, "
                f"{len(st.session_state.edges)}条依赖"
            )

        if run_btn and excel_path:
            _run_pipeline(excel_path)

        # 流水线状态摘要（仅在已完成时显示）
        if st.session_state.pipeline_status == "done" and st.session_state.indicators:
            inds = st.session_state.indicators
            edgs = st.session_state.edges or []
            st.success(
                f"数据就绪: {len(inds)}个指标 / {len(edgs)}条依赖"
            )

        st.divider()

        # ── 图谱浏览控件 ──
        st.subheader("🔍 图谱浏览")

        st.session_state.graph_view_mode = st.radio(
            "视图模式",
            ["sheet_overview", "indicator_detail"],
            format_func=lambda x: {
                "sheet_overview": "报表总览",
                "indicator_detail": "指标详情",
            }[x],
            horizontal=True,
        )

        if st.session_state.graph_view_mode == "indicator_detail":
            sheets = ["全部"]
            if st.session_state.indicators:
                sheets += sorted({ind["sheet"] for ind in st.session_state.indicators})
            st.session_state.selected_sheet = st.selectbox("筛选报表", sheets)
            st.session_state.search_indicator = st.text_input(
                "搜索指标名称", placeholder="如: 营业利润"
            )
            st.session_state.hop_depth = st.slider("邻域深度", 1, 3, 1)

        st.divider()

        # ── 连接状态 ──
        st.subheader("🔗 连接状态")
        neo4j_ok = test_neo4j_connection()
        st.session_state.neo4j_connected = neo4j_ok
        if neo4j_ok:
            st.success(f"Neo4j: 已连接")
        else:
            st.error("Neo4j: 未连接")
        st.info(f"LLM: {config.LLM_PROVIDER} / {config.LLM_MODEL}")


def _run_pipeline(excel_path: Path):
    """在 st.status 容器内执行三步流水线。"""
    with st.sidebar.status("执行流水线...", expanded=True) as status:
        # Step 1: 解析 Excel
        st.write("⏳ 步骤1: 解析Excel...")
        try:
            t0 = time.time()
            indicators = extract_indicators(excel_path)
            indicators = extract_values(excel_path, indicators)
            edges = parse_dependencies(indicators)
            save_indicators(indicators, config.INDICATORS_FILE)
            save_dependencies(edges, config.DEPENDENCIES_FILE)
            dur = time.time() - t0
            cross = sum(1 for e in edges if e.get("is_cross_sheet"))
            st.write(
                f"✅ 步骤1: {len(indicators)}个指标, {len(edges)}条依赖 "
                f"(跨表{cross}条) [{dur:.1f}s]"
            )
            st.session_state.indicators = indicators
            st.session_state.edges = edges
        except Exception as e:
            st.write(f"❌ 步骤1失败: {e}")
            status.update(label="流水线失败", state="error")
            st.session_state.pipeline_status = "error"
            return

        # Step 2: 加载 Neo4j
        st.write("⏳ 步骤2: 加载到Neo4j...")
        try:
            t0 = time.time()
            with GraphLoader(
                config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASSWORD
            ) as loader:
                loader.load_all(indicators, edges)
            dur = time.time() - t0
            st.write(f"✅ 步骤2: 加载完成 [{dur:.1f}s]")
        except Exception as e:
            st.write(f"❌ 步骤2失败: {e}")
            status.update(label="流水线失败", state="error")
            st.session_state.pipeline_status = "error"
            return

        # Step 3: 验证图谱
        st.write("⏳ 步骤3: 验证图谱...")
        try:
            t0 = time.time()
            with GraphValidator(
                config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASSWORD
            ) as v:
                vr = v.run_all_checks()
            dur = time.time() - t0
            checks = [
                vr["indicator_count"] >= 100,
                vr["sheet_count"] >= 10,
                vr["depends_on_count"] >= 100,
                vr["circular_paths_exist"],
                vr["irr_node_exists"],
            ]
            passed = sum(checks)
            st.write(f"✅ 步骤3: {passed}/{len(checks)}项通过 [{dur:.1f}s]")
            st.session_state.validation_results = vr
        except Exception as e:
            st.write(f"❌ 步骤3失败: {e}")
            status.update(label="流水线失败", state="error")
            st.session_state.pipeline_status = "error"
            return

        status.update(label="流水线完成 ✅", state="complete")
        st.session_state.pipeline_status = "done"


# ── 图谱区域 ──────────────────────────────────────────────────────────────────
def render_graph_area():
    st.subheader("📊 知识图谱")

    indicators = st.session_state.indicators
    edges = st.session_state.edges

    if not indicators or not edges:
        st.info("请先在左侧导入数据或加载已有数据，图谱将在此显示。")
        return

    # 验证指标行
    vr = st.session_state.validation_results
    if vr:
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("指标节点", vr["indicator_count"])
        c2.metric("依赖边", vr["depends_on_count"])
        c3.metric("跨表边", vr["cross_sheet_edge_count"])
        c4.metric("报表", vr["sheet_count"])
        c5.metric("孤立节点", vr["orphan_count"])
    else:
        c1, c2 = st.columns(2)
        c1.metric("指标节点", len(indicators))
        c2.metric("依赖边", len(edges))

    # 图例说明
    if st.session_state.graph_view_mode == "indicator_detail":
        st.caption(
            "🔴 搜索命中节点  🟠 跨表依赖边  🔴 循环依赖边  ⚫ 同表依赖边"
        )

    # 构建并嵌入图
    with st.spinner("渲染图谱..."):
        if st.session_state.graph_view_mode == "sheet_overview":
            html = build_sheet_overview_graph(indicators, edges)
        else:
            html = build_indicator_detail_graph(
                indicators,
                edges,
                filter_sheet=st.session_state.selected_sheet,
                search_name=st.session_state.search_indicator or None,
                hop_depth=st.session_state.hop_depth,
            )

    components.html(html, height=520, scrolling=False)


# ── 对话区域 ──────────────────────────────────────────────────────────────────
def render_chat_area():
    st.subheader("💬 智能问答")

    if not st.session_state.neo4j_connected:
        st.warning("Neo4j 未连接，请先确保数据库运行并在左侧确认连接状态。")
        return

    # 懒初始化 FinancialGraphChat
    if st.session_state.chat_obj is None:
        try:
            st.session_state.chat_obj = FinancialGraphChat(
                neo4j_uri=config.NEO4J_URI,
                neo4j_user=config.NEO4J_USER,
                neo4j_password=config.NEO4J_PASSWORD,
                llm_provider=config.LLM_PROVIDER,
                llm_api_key=config.LLM_API_KEY,
                llm_base_url=config.LLM_BASE_URL,
                llm_model=config.LLM_MODEL,
            )
        except Exception as e:
            st.error(f"LLM 初始化失败: {e}")
            return

    # 渲染历史对话
    for msg in st.session_state.chat_messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and not msg.get("error"):
                if msg.get("cypher"):
                    with st.expander("查看 Cypher 查询"):
                        st.code(msg["cypher"], language="cypher")
                if msg.get("results") is not None:
                    with st.expander(f"查看原始数据 ({len(msg['results'])} 条)"):
                        st.json(msg["results"])

    # 输入框
    if question := st.chat_input("请输入您的问题，例如：营业利润依赖哪些指标？"):
        # 显示用户消息
        with st.chat_message("user"):
            st.markdown(question)
        st.session_state.chat_messages.append({"role": "user", "content": question})

        # 调用 LLM
        with st.chat_message("assistant"):
            with st.spinner("思考中..."):
                result = st.session_state.chat_obj.ask(question)

            if "error" in result:
                st.error(result["error"])
                st.session_state.chat_messages.append(
                    {"role": "assistant", "content": result["error"], "error": True}
                )
            else:
                st.markdown(result["answer"])
                if result.get("cypher"):
                    with st.expander("查看 Cypher 查询"):
                        st.code(result["cypher"], language="cypher")
                if result.get("results") is not None:
                    with st.expander(f"查看原始数据 ({len(result['results'])} 条)"):
                        st.json(result["results"])
                st.session_state.chat_messages.append({
                    "role": "assistant",
                    "content": result["answer"],
                    "cypher": result.get("cypher"),
                    "results": result.get("results"),
                })


# ── 主布局 ────────────────────────────────────────────────────────────────────
def main():
    init_session_state()
    render_sidebar()

    graph_container = st.container()
    st.divider()
    chat_container = st.container()

    with graph_container:
        render_graph_area()

    with chat_container:
        render_chat_area()


if __name__ == "__main__":
    main()
