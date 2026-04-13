"""
pages/2_task_detail.py — 4-step pipeline view for a single task.
"""
import sys
import json
import time
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd

import config
from src.task.manager import TaskManager
from src.task.pipeline import PipelineRunner
from src.task.models import StepInfo

st.set_page_config(page_title="任务详情", page_icon="🔬", layout="wide")


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_task_manager() -> TaskManager:
    if "task_manager" not in st.session_state:
        st.session_state.task_manager = TaskManager(config.TASKS_DIR)
    return st.session_state.task_manager


def get_pipeline_runner() -> PipelineRunner:
    if "pipeline_runner" not in st.session_state:
        st.session_state.pipeline_runner = PipelineRunner(get_task_manager())
    return st.session_state.pipeline_runner


def get_llm_callable():
    from src.llm.cypher_generator import _make_llm_client
    return _make_llm_client(
        config.LLM_PROVIDER,
        config.LLM_API_KEY,
        config.LLM_BASE_URL,
        config.LLM_MODEL,
    )


def step_status_color(status: str) -> str:
    return {"pending": "gray", "running": "blue", "done": "green", "error": "red"}.get(status, "gray")


def step_status_label(status: str) -> str:
    return {"pending": "待开始", "running": "进行中", "done": "已完成", "error": "出错"}.get(status, status)


def render_step_header(num: int, title: str, step_info: StepInfo):
    color = step_status_color(step_info.status)
    label = step_status_label(step_info.status)
    st.markdown(f"### Step {num}: {title}  :{color}[{label}]")
    if step_info.status == "running":
        st.progress(step_info.progress_pct, text=step_info.progress_msg)
    elif step_info.status == "error":
        st.error(f"错误：{step_info.error}")
    elif step_info.status == "done" and step_info.progress_msg:
        st.success(step_info.progress_msg)


# ── Graph visualization helpers ────────────────────────────────────────────────

def build_sheet_overview_graph(task_id: str) -> str:
    from pyvis.network import Network
    from src.graph.validator import GraphValidator

    net = Network(height="420px", width="100%", directed=True, bgcolor="#1a1a2e")
    net.set_options("""{"nodes": {"font": {"color": "#ffffff"}}, "edges": {"color": {"color": "#aaaaaa"}, "font": {"color": "#cccccc"}}, "physics": {"enabled": true, "stabilization": {"iterations": 100}}}""")

    try:
        with GraphValidator(config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASSWORD, task_id=task_id) as v:
            sheets = v._query(f"MATCH (s:Sheet) WHERE s.task_id = '{task_id}' RETURN s.name AS name, s.description AS desc")
            feeds = v._query(f"MATCH (a:Sheet)-[:FEEDS_INTO]->(b:Sheet) WHERE a.task_id = '{task_id}' RETURN a.name AS from_s, b.name AS to_s")

        for s in sheets:
            net.add_node(s["name"], label=s["name"][:15], title=s.get("desc", ""), color="#4a90d9")
        for f in feeds:
            if f["from_s"] and f["to_s"]:
                net.add_edge(f["from_s"], f["to_s"])
    except Exception as e:
        net.add_node("error", label=f"Neo4j连接失败: {e}", color="red")

    return net.generate_html()


def build_query_result_graph(results: list[dict]) -> str | None:
    """Build pyvis graph from Cypher query results.

    Handles three result shapes:
      - Path/chain: any column whose value is a list of strings
      - Edge pair: two columns whose values look like indicator names
      - Single nodes: one or more string columns
    Key names may be English or Chinese aliases, so we inspect values, not keys.
    """
    if not results:
        return None

    from pyvis.network import Network
    net = Network(height="460px", width="100%", directed=True, bgcolor="#1a1a2e")
    net.set_options("""{"nodes": {"font": {"color": "#ffffff"}}, "edges": {"color": {"color": "#aaaaaa"}, "font": {"color": "#cccccc"}}, "physics": {"enabled": true, "solver": "forceAtlas2Based"}}""")

    nodes_added: set = set()
    edges_added: set = set()

    def add_node(name: str, tooltip: str = "", color: str = "#e8a838"):
        if name and name not in nodes_added:
            nodes_added.add(name)
            label = name[:25] if len(name) > 25 else name
            net.add_node(name, label=label, title=(tooltip or name), color=color)

    def add_edge(src: str, dst: str):
        key = (src, dst)
        if key not in edges_added and src != dst:
            edges_added.add(key)
            net.add_edge(src, dst)

    def _str_val(v) -> str:
        """Return a clean string or empty string."""
        if v is None:
            return ""
        s = str(v).strip()
        return "" if s in ("None", "null", "") else s

    def _is_name_like(s: str) -> bool:
        """Heuristic: looks like an indicator/sheet name (not a raw formula or long text)."""
        return bool(s) and len(s) <= 60 and not s.startswith("=")

    for row in results[:80]:
        # ── 1. Chain / path: any value that is a non-empty list ───────────────
        chain = None
        for v in row.values():
            if isinstance(v, list) and len(v) >= 2:
                chain = v
                break
        if chain is not None:
            prev = None
            for node_name in chain:
                s = _str_val(node_name)
                if not s:
                    continue
                add_node(s)
                if prev:
                    add_edge(prev, s)
                prev = s
            continue

        # ── 2. Collect candidate name columns (prefer keys with "name"/sheet) ─
        # Priority 1: keys containing "name" (English or pinyin)
        name_keys = [k for k in row if "name" in str(k).lower()]
        sheet_keys = [k for k in row if "sheet" in str(k).lower()]

        names = [_str_val(row[k]) for k in name_keys]
        names = [n for n in names if _is_name_like(n)]
        sheets = [_str_val(row[k]) for k in sheet_keys if _str_val(row[k])]

        # Priority 2: fallback — any short string value that looks like a name
        if not names:
            for k, v in row.items():
                s = _str_val(v)
                if _is_name_like(s) and isinstance(v, str):
                    names.append(s)

        if len(names) >= 2:
            # Treat first two as a directed edge (source → target)
            add_node(names[0], sheets[0] if sheets else "")
            add_node(names[1], sheets[1] if len(sheets) > 1 else "")
            add_edge(names[0], names[1])
        elif names:
            add_node(names[0], sheets[0] if sheets else "")

    if len(nodes_added) < 1:
        return None

    return net.generate_html()


# ── Coverage UI helper ─────────────────────────────────────────────────────────

def _render_coverage_ui(task_id: str, tm, runner, get_llm_callable_fn):
    """Render the coverage report section inside the Step 2 done block."""
    from src.parser.coverage_scanner import load_coverage, format_coverage_feedback
    from src.parser.audit_exporter import export_audit_workbook

    coverage = load_coverage(tm.get_coverage_path(task_id))

    if coverage is None:
        # Old task without coverage.json — offer manual scan
        if st.button("🔍 运行覆盖率扫描", key="run_coverage"):
            import json as _json
            from src.parser.coverage_scanner import scan_coverage, save_coverage
            excel_path = tm.get_excel_path(task_id)
            config_data = _json.loads(tm.get_config_path(task_id).read_text(encoding="utf-8"))
            indicators = _json.loads(tm.get_indicators_path(task_id).read_text(encoding="utf-8"))
            with st.spinner("扫描中..."):
                cov = scan_coverage(excel_path, config_data.get("sheet_configs", {}), indicators)
                save_coverage(cov, tm.get_coverage_path(task_id))
            st.rerun()
        return

    summary = coverage["summary"]
    pct = summary["coverage_pct"]
    total = summary["total_content_rows"]
    extracted = summary["extracted_rows"]
    broken = summary["broken_deps"]

    # ── Summary bar ───────────────────────────────────────────────────────────
    st.progress(pct, text=f"提取了 {extracted}/{total} 行 ({pct:.1%})")
    if broken > 0:
        st.warning(f"{broken} 条公式依赖引用了未提取的行")
    elif pct >= 1.0:
        st.success("覆盖率 100%，无断裂依赖")

    # ── Per-sheet detail ──────────────────────────────────────────────────────
    sheet_rows = []
    for sname, sd in coverage["sheets"].items():
        reasons = {}
        for r in sd["rows"]:
            if r["status"] == "skipped":
                cat = r.get("reason", "unknown")
                if cat.startswith("skip_pattern:"):
                    cat = "skip_pattern"
                reasons[cat] = reasons.get(cat, 0) + 1
        sheet_rows.append({
            "工作表": sname,
            "总行数": sd["total"],
            "已提取": sd["extracted"],
            "覆盖率": f"{sd['coverage_pct']:.1%}",
            "跳过(表头)": reasons.get("header_row", 0),
            "跳过(模式)": reasons.get("skip_pattern", 0),
            "跳过(无中文)": reasons.get("not_meaningful_name", 0),
            "跳过(未知)": reasons.get("unknown", 0),
        })
    if pct < 0.99:
        st.markdown("**逐Sheet覆盖率明细：**")
        st.dataframe(pd.DataFrame(sheet_rows), use_container_width=True, hide_index=True)

    # ── Action buttons ────────────────────────────────────────────────────────
    col_dl, col_fix = st.columns(2)

    with col_dl:
        import json as _json
        try:
            indicators = _json.loads(tm.get_indicators_path(task_id).read_text(encoding="utf-8"))
            audit_bytes = export_audit_workbook(coverage, indicators)
            st.download_button(
                "📥 下载审计底稿",
                data=audit_bytes,
                file_name=f"audit_{task_id[:8]}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_audit",
            )
        except Exception as e:
            st.caption(f"审计底稿生成失败：{e}")

    with col_fix:
        if pct < 0.95:
            if st.button("🔧 自动修复配置", key="auto_repair_step2",
                         help="将覆盖率报告作为反馈发给LLM，重新生成配置后自动重新解析"):
                feedback = format_coverage_feedback(coverage, threshold=0.90)
                runner.run_step1(task_id, get_llm_callable_fn(), feedback=feedback)
                st.session_state["auto_run_step2_after_step1"] = True
                st.rerun()
        else:
            st.caption("覆盖率已达标，无需自动修复")

    # Auto-trigger step2 after step1 finishes (set by auto-repair button)
    if st.session_state.pop("auto_run_step2_after_step1", False):
        # Will be checked on next render when step1 is done
        st.session_state["_pending_auto_step2"] = True


# ── Main page ──────────────────────────────────────────────────────────────────

task_id = st.session_state.get("current_task_id")
if not task_id:
    st.warning("请先从任务列表选择一个任务。")
    if st.button("前往任务列表"):
        st.switch_page("pages/1_task_list.py")
    st.stop()

tm = get_task_manager()
runner = get_pipeline_runner()
meta = tm.get_task(task_id)

if not meta:
    st.error(f"任务不存在：{task_id}")
    st.stop()

# Detect stale "running" states left over from a previous Streamlit process.
# A step is truly stale only if:
#   (a) no thread is alive in this session, AND
#   (b) meta.json hasn't been updated in the last 45 seconds
#       (if it was updated recently, the thread is alive in another browser session)
def _meta_age_seconds(meta) -> float:
    try:
        last = datetime.fromisoformat(meta.updated_at)
        return (datetime.now() - last).total_seconds()
    except Exception:
        return 9999.0

_stale = False
for _step_num, _step_attr in [(1, "step1"), (2, "step2"), (3, "step3")]:
    _info = getattr(meta, _step_attr)
    if _info.status == "running" and not runner.is_running(task_id, _step_num):
        if _meta_age_seconds(meta) > 45:
            # Truly stale: thread dead and no recent heartbeat
            setattr(meta, _step_attr, StepInfo(status="pending", progress_msg=""))
            _stale = True
        # else: thread is alive in another session — leave it running
if _stale:
    tm.save_task(meta)
    meta = tm.get_task(task_id)

# any_running: in-session threads OR a step that is "running" on disk
# (covers the page-refresh case where the thread lives in another session)
any_running = (
    runner.is_running(task_id, 1) or runner.is_running(task_id, 2) or runner.is_running(task_id, 3)
    or meta.step1.status == "running"
    or meta.step2.status == "running"
    or meta.step3.status == "running"
)
# Always reload meta from disk when something is running (get fresh progress)
if any_running:
    meta = tm.get_task(task_id)

st.title(f"🔬 {meta.name}")
st.caption(f"任务ID: {task_id[:8]}...  |  文件: {meta.excel_filename}  |  创建: {meta.created_at[:10]}")

if st.button("← 返回任务列表"):
    st.switch_page("pages/1_task_list.py")

st.divider()

# ── Step 1: LLM Config Generation ─────────────────────────────────────────────
with st.expander("Step 1: LLM分析Excel → 生成解析配置", expanded=(meta.step1.status in ("pending", "running", "error"))):
    render_step_header(1, "LLM分析Excel → 生成解析配置", meta.step1)

    config_path = tm.get_config_path(task_id)
    existing_config = None
    if config_path.exists():
        try:
            existing_config = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    col_btn1, col_btn2, col_stop1 = st.columns([1, 3, 1])
    with col_btn1:
        if meta.step1.status != "running":
            if st.button("🤖 生成配置", key="gen_config"):
                runner.run_step1(task_id, get_llm_callable())
                st.rerun()
    with col_stop1:
        if meta.step1.status == "running":
            if st.button("⏹ 停止", key="stop_step1"):
                runner.stop_step(task_id, 1)
                st.rerun()
    with col_btn2:
        if existing_config and meta.step1.status != "running":
            feedback = st.text_input(
                "反馈给AI（可选）",
                placeholder="例如：Sheet2的name_col应该是D列，不是C列",
                key="step1_feedback",
            )
            if st.button("🔄 根据反馈重新生成", key="regen_config"):
                runner.run_step1(task_id, get_llm_callable(), feedback=feedback or None)
                st.rerun()

    if existing_config:
        st.markdown("**当前配置（可直接编辑）：**")
        config_text = st.text_area(
            "解析配置 JSON",
            value=json.dumps(existing_config, ensure_ascii=False, indent=2),
            height=300,
            key="config_editor",
        )
        if st.button("💾 保存配置修改", key="save_config"):
            try:
                new_config = json.loads(config_text)
                config_path.write_text(
                    json.dumps(new_config, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                if meta.step1.status != "done":
                    meta.step1 = StepInfo(status="done", progress_msg="配置已手动保存", progress_pct=1.0)
                    tm.save_task(meta)
                st.success("配置已保存")
                st.rerun()
            except json.JSONDecodeError as e:
                st.error(f"JSON格式错误：{e}")

    log_text = tm.read_log(task_id, step=1)
    if log_text:
        st.text_area("执行日志", value=log_text, height=150, disabled=True, key="log_step1")

# ── Step 2: Parse Excel → JSON ─────────────────────────────────────────────────
# Auto-trigger step2 after auto-repair completes step1
if st.session_state.get("_pending_auto_step2") and meta.step1.status == "done" and meta.step2.status != "running":
    st.session_state.pop("_pending_auto_step2", None)
    runner.run_step2(task_id)
    st.rerun()

with st.expander("Step 2: 解析Excel → JSON", expanded=(meta.step2.status in ("pending", "running", "error") and meta.step1.status == "done")):
    render_step_header(2, "解析Excel → JSON", meta.step2)

    if meta.step1.status != "done":
        st.info("请先完成 Step 1（生成解析配置）")
    else:
        col_s2a, col_s2b, col_s2stop = st.columns([1, 1, 1])
        with col_s2a:
            if meta.step2.status != "running":
                if st.button("▶ 开始解析", key="start_step2"):
                    runner.run_step2(task_id)
                    st.rerun()
        with col_s2b:
            if meta.step2.status == "done":
                if st.button("🔄 重新解析", key="redo_step2"):
                    runner.run_step2(task_id)
                    st.rerun()
        with col_s2stop:
            if meta.step2.status == "running":
                if st.button("⏹ 停止", key="stop_step2"):
                    runner.stop_step(task_id, 2)
                    st.rerun()

        if meta.step2.status == "done":
            _render_coverage_ui(task_id, tm, runner, get_llm_callable)

        log_text = tm.read_log(task_id, step=2)
        if log_text:
            st.text_area("解析日志", value=log_text, height=150, disabled=True, key="log_step2")

# ── Step 3: Load to Neo4j ──────────────────────────────────────────────────────
with st.expander("Step 3: 加载到Neo4j", expanded=(meta.step3.status in ("pending", "running", "error") and meta.step2.status == "done")):
    render_step_header(3, "加载到Neo4j", meta.step3)

    if meta.step2.status != "done":
        st.info("请先完成 Step 2（解析Excel）")
    else:
        if meta.step3.status == "pending":
            st.info("Neo4j 数据已清空，点击「▶ 开始加载」重新写入。")
        col_s3a, col_s3b, col_s3stop = st.columns([1, 1, 1])
        with col_s3a:
            if meta.step3.status != "running":
                if st.button("▶ 开始加载", key="start_step3"):
                    runner.run_step3(task_id, config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASSWORD)
                    st.rerun()
        with col_s3b:
            if meta.step3.status == "done":
                if st.button("🗑 清空并重新加载", key="reload_step3"):
                    try:
                        runner.clear_neo4j_task(task_id, config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASSWORD)
                        meta.step3 = StepInfo(status="pending")
                        tm.save_task(meta)
                        tm.clear_log(task_id, step=3)
                        st.rerun()
                    except Exception as e:
                        st.error(f"清空失败：{e}")
        with col_s3stop:
            if meta.step3.status == "running":
                if st.button("⏹ 停止", key="stop_step3"):
                    runner.stop_step(task_id, 3)
                    st.rerun()

        log_text = tm.read_log(task_id, step=3)
        if log_text:
            st.text_area("加载日志", value=log_text, height=150, disabled=True, key="log_step3")

    # ── Modify parameters (only when step3 is done) ─────────────────────────────
    if meta.step3.status == "done":
        st.markdown("---")
        col_mp1, col_mp2 = st.columns([1, 5])
        with col_mp1:
            if st.button("✏️ 修改参数值", key="go_modify_params"):
                st.switch_page("pages/3_modify_params.py")
        with col_mp2:
            st.caption("修改参数输入表中的指标值，自动触发级联重算并更新Neo4j")

# ── Step 4: Chat + Graph ───────────────────────────────────────────────────────
st.divider()
st.markdown("### Step 4: 对话查询 + 图谱可视化")

if meta.step3.status != "done":
    st.info("请先完成 Step 3（加载到Neo4j）后才能进行对话查询。")
else:
    from src.task.chat_store import ChatStore

    @st.cache_resource
    def get_chat_store():
        return ChatStore(config.CHAT_DB)

    store = get_chat_store()
    chat_key = f"chat_obj_{task_id}"

    # ── Active conversation: persist conv_id in session_state ─────────────────
    conv_id_key = f"active_conv_{task_id}"
    if conv_id_key not in st.session_state:
        # Resume the most recent conversation for this task, or create one
        convs = store.get_conversations(task_id)
        if convs:
            st.session_state[conv_id_key] = convs[0]["id"]
        else:
            st.session_state[conv_id_key] = store.new_conversation(task_id)

    conv_id = st.session_state[conv_id_key]

    # Ensure the FinancialGraphChat object exists (recreate after restart)
    if chat_key not in st.session_state:
        from src.llm.cypher_generator import FinancialGraphChat
        st.session_state[chat_key] = FinancialGraphChat(
            neo4j_uri=config.NEO4J_URI,
            neo4j_user=config.NEO4J_USER,
            neo4j_password=config.NEO4J_PASSWORD,
            llm_provider=config.LLM_PROVIDER,
            llm_api_key=config.LLM_API_KEY,
            llm_base_url=config.LLM_BASE_URL,
            llm_model=config.LLM_MODEL,
            task_id=task_id,
        )
        # Restore LLM history from DB so context is preserved across restarts
        for m in store.get_messages(conv_id):
            if m["role"] in ("user", "assistant"):
                st.session_state[chat_key].history.append(
                    {"role": m["role"], "content": m["content"]}
                )
        if len(st.session_state[chat_key].history) > 20:
            st.session_state[chat_key].history = st.session_state[chat_key].history[-20:]

    # ── Chat header: title + new-conversation button ───────────────────────────
    hdr_col, btn_col = st.columns([5, 1])
    with hdr_col:
        st.markdown("**💬 对话查询**")
    with btn_col:
        if st.button("🆕 新对话", key="new_chat"):
            new_id = store.new_conversation(task_id)
            st.session_state[conv_id_key] = new_id
            st.session_state.pop(chat_key, None)
            st.rerun()

    # ── History sidebar ────────────────────────────────────────────────────────
    all_convs = store.get_conversations(task_id)
    past_convs = [c for c in all_convs if c["id"] != conv_id]
    if past_convs:
        with st.expander(f"📋 历史对话（共 {len(past_convs)} 轮）", expanded=False):
            for c in past_convs:
                col_title, col_btn, col_del = st.columns([5, 2, 1])
                with col_title:
                    st.markdown(f"**{c['title'][:40]}**  \n{c['created_at'][:16]}")
                with col_btn:
                    if st.button("切换", key=f"switch_{c['id']}"):
                        st.session_state[conv_id_key] = c["id"]
                        st.session_state.pop(chat_key, None)
                        st.rerun()
                with col_del:
                    if st.button("🗑", key=f"del_{c['id']}"):
                        store.delete_conversation(c["id"])
                        st.rerun()
                # Show message preview
                msgs = store.get_messages(c["id"])
                for msg in msgs[:6]:
                    if msg["role"] == "user":
                        st.markdown(f"> 🧑 {msg['content'][:80]}")
                    else:
                        preview = msg["content"][:200] + ("..." if len(msg["content"]) > 200 else "")
                        st.markdown(f"> 🤖 {preview}")
                        if msg.get("cypher"):
                            st.code(msg["cypher"], language="cypher")
                st.divider()

    # ── Current conversation ───────────────────────────────────────────────────
    current_msgs = store.get_messages(conv_id)

    chat_container = st.container(height=400)
    with chat_container:
        for i, msg in enumerate(current_msgs):
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("cypher"):
                    with st.expander("Cypher"):
                        st.code(msg["cypher"], language="cypher")
                if msg.get("results"):
                    if st.button("📊 展示图谱", key=f"show_graph_{task_id}_{i}"):
                        html = build_query_result_graph(msg["results"])
                        if html:
                            st.session_state[f"query_graph_{task_id}"] = html
                            st.toast("图谱已更新，请查看「查询结果图」标签")
                        else:
                            st.toast("该查询结果无法可视化（无节点数据）")
                    with st.expander(f"原始结果 ({len(msg['results'])} 行)"):
                        st.json(msg["results"][:20])

    question = st.chat_input("输入问题，例如：净利润依赖哪些指标？")
    if question:
        # Persist user message
        store.add_message(conv_id, "user", question)
        # Auto-title the conversation from first user message
        if store.message_count(conv_id) <= 1:
            store.update_title(conv_id, question[:40])

        with st.spinner("查询中..."):
            result = st.session_state[chat_key].ask(question)

        if "error" in result:
            store.add_message(conv_id, "assistant", f"查询出错：{result['error']}")
        else:
            store.add_message(
                conv_id,
                "assistant",
                result["answer"],
                cypher=result.get("cypher"),
                results=result.get("results"),
            )
            graph_html = build_query_result_graph(result.get("results", []))
            if graph_html:
                st.session_state[f"query_graph_{task_id}"] = graph_html

        st.rerun()

    st.divider()

    # ── Graph (full width) ─────────────────────────────────────────────────────
    st.markdown("**📊 图谱可视化**")
    graph_tab1, graph_tab2 = st.tabs(["报表总览", "查询结果图"])

    with graph_tab1:
        if st.button("刷新报表图谱", key="refresh_overview"):
            st.session_state.pop(f"overview_html_{task_id}", None)
        cache_key = f"overview_html_{task_id}"
        if cache_key not in st.session_state:
            with st.spinner("加载图谱..."):
                st.session_state[cache_key] = build_sheet_overview_graph(task_id)
        components.html(st.session_state[cache_key], height=450, scrolling=False)

    with graph_tab2:
        result_html = st.session_state.get(f"query_graph_{task_id}")
        if result_html:
            components.html(result_html, height=490, scrolling=False)
        else:
            st.caption("发起对话查询后，点击消息中的「📊 展示图谱」按钮可在此显示。")

# ── Auto-refresh (must be last, after all content is rendered) ─────────────────
if any_running:
    time.sleep(1)
    st.rerun()
