"""
app.py

CLI conversational interface for the financial graph Q&A system.
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
from src.llm.cypher_generator import FinancialGraphChat
from src.llm.response_formatter import format_results

logging.basicConfig(level=logging.WARNING)  # Quiet in interactive mode

BANNER = """
╔══════════════════════════════════════════════════════════╗
║     抽水蓄能财务模型 · 图知识库 · 智能问答系统           ║
║     Pumped-Storage Financial Graph Q&A                   ║
╚══════════════════════════════════════════════════════════╝

示例问题：
  • 营业利润依赖哪些指标？
  • 贷款利率变化会影响哪些指标？
  • 从装机容量到资本金IRR的计算路径是什么？
  • 资产负债表的勾稽关系是什么？
  • 三免三减半政策如何影响净利润？
  • 哪些指标存在循环依赖？

输入 'quit' 或 'exit' 退出，'clear' 清除对话历史。
"""


def main():
    print(BANNER)

    try:
        chat = FinancialGraphChat(
            neo4j_uri=config.NEO4J_URI,
            neo4j_user=config.NEO4J_USER,
            neo4j_password=config.NEO4J_PASSWORD,
            llm_provider=config.LLM_PROVIDER,
            llm_api_key=config.LLM_API_KEY,
            llm_base_url=config.LLM_BASE_URL,
            llm_model=config.LLM_MODEL,
        )
    except Exception as e:
        print(f"连接失败: {e}")
        print("请检查 .env 文件中的 Neo4j 和 LLM 配置。")
        sys.exit(1)

    with chat:
        while True:
            try:
                question = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not question:
                continue
            if question.lower() in ("quit", "exit", "q"):
                print("再见！")
                break
            if question.lower() == "clear":
                chat.history.clear()
                print("对话历史已清除。")
                continue

            result = chat.ask(question)

            if "error" in result:
                print(f"\n错误: {result['error']}")
                continue

            # Show the Cypher (optional, for transparency)
            if result.get("cypher") and not result["cypher"].startswith("--"):
                print(f"\n[Cypher] {result['cypher'][:120]}{'...' if len(result['cypher']) > 120 else ''}")

            # Show answer
            print(f"\n{result['answer']}")


if __name__ == "__main__":
    main()
