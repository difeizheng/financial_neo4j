"""
pages/1_task_list.py — Task management: create, view, delete tasks.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import config
from src.task.manager import TaskManager

st.set_page_config(page_title="任务列表", page_icon="📋", layout="wide")


def get_task_manager() -> TaskManager:
    if "task_manager" not in st.session_state:
        st.session_state.task_manager = TaskManager(config.TASKS_DIR)
    return st.session_state.task_manager


def status_badge(status: str) -> str:
    colors = {
        "pending": "🔘",
        "running": "🔄",
        "done": "✅",
        "error": "❌",
    }
    return colors.get(status, "⬜")


def task_overall_status(meta) -> str:
    """Derive overall task status from step statuses."""
    if meta.step3.status == "done":
        return "已完成加载，可对话"
    if meta.step3.status == "running":
        return "Step 3 加载中..."
    if meta.step3.status == "error":
        return "Step 3 出错"
    if meta.step2.status == "done":
        return "Step 2 完成，待加载"
    if meta.step2.status == "running":
        return "Step 2 解析中..."
    if meta.step2.status == "error":
        return "Step 2 出错"
    if meta.step1.status == "done":
        return "Step 1 完成，待解析"
    if meta.step1.status == "running":
        return "Step 1 分析中..."
    if meta.step1.status == "error":
        return "Step 1 出错"
    return "待开始"


st.title("📋 任务列表")

tm = get_task_manager()

# ── Create new task ────────────────────────────────────────────────────────────
with st.expander("➕ 创建新任务", expanded=False):
    with st.form("create_task_form"):
        task_name = st.text_input("任务名称", placeholder="例如：2024年度财务报表分析")
        uploaded_file = st.file_uploader("上传财务报表 Excel", type=["xlsx", "xls"])
        submitted = st.form_submit_button("创建任务")

    if submitted:
        if not task_name.strip():
            st.error("请输入任务名称")
        elif uploaded_file is None:
            st.error("请上传Excel文件")
        else:
            meta = tm.create_task(
                name=task_name.strip(),
                excel_bytes=uploaded_file.read(),
                filename=uploaded_file.name,
            )
            st.success(f"任务已创建：{meta.name}（ID: {meta.task_id[:8]}...）")
            st.rerun()

# ── Task list ──────────────────────────────────────────────────────────────────
st.divider()
tasks = tm.list_tasks()

if not tasks:
    st.info("暂无任务。点击上方「创建新任务」开始。")
else:
    st.caption(f"共 {len(tasks)} 个任务")

    for meta in tasks:
        col1, col2, col3, col4, col5 = st.columns([3, 2, 1, 1, 1])

        with col1:
            st.markdown(f"**{meta.name}**")
            st.caption(f"文件：{meta.excel_filename}  |  创建：{meta.created_at[:10]}")

        with col2:
            st.caption(task_overall_status(meta))
            # Step status badges
            s1 = status_badge(meta.step1.status)
            s2 = status_badge(meta.step2.status)
            s3 = status_badge(meta.step3.status)
            st.caption(f"S1{s1} S2{s2} S3{s3}")

        with col3:
            if meta.indicator_count:
                st.metric("指标", meta.indicator_count)

        with col4:
            if st.button("进入", key=f"enter_{meta.task_id}"):
                st.session_state.current_task_id = meta.task_id
                st.switch_page("pages/2_task_detail.py")

        with col5:
            if st.button("删除", key=f"del_{meta.task_id}", type="secondary"):
                st.session_state[f"confirm_delete_{meta.task_id}"] = True

        # Confirm delete
        if st.session_state.get(f"confirm_delete_{meta.task_id}"):
            st.warning(f"确认删除任务「{meta.name}」？此操作不可撤销。")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("确认删除", key=f"confirm_{meta.task_id}", type="primary"):
                    tm.delete_task(meta.task_id)
                    st.session_state.pop(f"confirm_delete_{meta.task_id}", None)
                    st.rerun()
            with c2:
                if st.button("取消", key=f"cancel_{meta.task_id}"):
                    st.session_state.pop(f"confirm_delete_{meta.task_id}", None)
                    st.rerun()

        st.divider()
