"""
streamlit_app.py — Multi-task financial report analysis platform entry point.
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st

st.set_page_config(
    page_title="财务报表知识图谱",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📊 财务报表知识图谱分析平台")
st.markdown("""
欢迎使用财务报表知识图谱分析平台。

**使用流程：**
1. 在 **任务列表** 页面创建分析任务，上传财务报表Excel
2. 进入任务详情，按步骤完成分析流水线
3. 在 **Step 4** 中通过自然语言对话查询财务指标关系

请从左侧导航栏选择页面开始。
""")
