"""
config_generator.py

Uses an LLM to analyze Excel structure metadata and generate a parsing config
compatible with the SHEET_CONFIGS format used by indicator_registry.py.
"""
from __future__ import annotations
import json
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Reference example from the pumped-storage model (few-shot)
_EXAMPLE_CONFIG = {
    "sheet_configs": {
        "参数输入表": {
            "name_col": "D",
            "formula_col": "I",
            "unit_col": "H",
            "number_col": "C",
            "category_col": "B",
            "header_rows": [1, 2, 3, 4, 5],
            "skip_patterns": ["下拉菜单", "第"],
            "is_input": True,
        },
        "表1-辅助计算表": {
            "name_col": "C",
            "formula_col": "F",
            "unit_col": "E",
            "number_col": "B",
            "category_col": None,
            "header_rows": [1, 2, 3],
            "skip_patterns": ["合作期第", "建设期第"],
            "is_input": False,
        },
    },
    "sheet_categories": {
        "参数输入表": "参数输入",
        "表1-辅助计算表": "辅助计算",
    },
    "circular_groups": [],
}

_SYSTEM_PROMPT = """你是一个专业的财务Excel分析助手。你的任务是分析Excel文件的结构，并生成一个JSON格式的解析配置文件。

配置文件的格式如下：
{
  "sheet_configs": {
    "工作表名称": {
      "name_col": "C",          // 指标名称所在列（字母）
      "formula_col": "F",       // 公式/数值起始列（字母）
      "unit_col": "E",          // 单位列（字母，可为null）
      "number_col": "B",        // 序号列（字母，可为null）
      "category_col": null,     // 分类列（字母，可为null，通常只有参数输入表有）
      "header_rows": [1, 2, 3], // 表头行号列表（1-based）
      "skip_patterns": ["合作期第", "建设期第"], // 需要跳过的行的关键词
      "is_input": false         // 是否为参数输入表
    }
  },
  "sheet_categories": {
    "工作表名称": "利润表"       // 工作表的财务类别
  },
  "circular_groups": []         // 循环依赖组（通常为空，除非你能识别出来）
}

财务类别可以是：参数输入、辅助计算、投资估算、融资与债务、折旧摊销、成本费用、收入与税金、利润表、现金流量表、资产负债表、其他

判断规则：
1. name_col：找到包含中文指标名称的列（通常是B、C或D列）
2. formula_col：找到第一个包含公式（=开头）或数值的数据列
3. header_rows：找到表头行（通常是前1-5行，包含标题、单位说明等）
4. skip_patterns：找到需要跳过的行的特征词（如年份展开行"合作期第X年"）
5. is_input：只有参数输入表为true

只输出JSON，不要有任何其他文字。"""


def generate_config(
    excel_metadata: dict,
    llm_callable: Callable,
    feedback: Optional[str] = None,
) -> dict:
    """
    Send Excel metadata to LLM and get back a SHEET_CONFIGS-compatible dict.

    excel_metadata: output of analyze_excel()
    llm_callable: callable(messages, system) -> str
    feedback: optional user feedback for re-generation
    """
    # Build the user message
    meta_json = json.dumps(excel_metadata, ensure_ascii=False, indent=2)
    example_json = json.dumps(_EXAMPLE_CONFIG, ensure_ascii=False, indent=2)

    user_content = f"""请分析以下Excel文件的结构，并生成解析配置文件。

## 参考示例（抽水蓄能财务模型的配置）：
{example_json}

## 待分析的Excel结构：
{meta_json}
"""

    if feedback:
        user_content += f"""
## 用户反馈（请根据此反馈调整配置）：
{feedback}
"""

    user_content += "\n请输出JSON格式的解析配置，只输出JSON，不要有其他文字。"

    messages = [{"role": "user", "content": user_content}]

    logger.info("Calling LLM to generate parsing config...")
    response = llm_callable(messages, _SYSTEM_PROMPT)

    # Parse JSON from response
    config = _parse_json_response(response)
    logger.info(f"Config generated: {len(config.get('sheet_configs', {}))} sheets")
    return config


def _parse_json_response(response: str) -> dict:
    """Extract JSON from LLM response, handling markdown code blocks."""
    text = response.strip()

    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```) and last line (```)
        inner = "\n".join(lines[1:])
        if inner.rstrip().endswith("```"):
            inner = inner.rstrip()[:-3].rstrip()
        text = inner

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}")
        logger.error(f"Response was: {text[:500]}")
        raise ValueError(f"LLM返回的配置不是有效的JSON格式: {e}") from e
