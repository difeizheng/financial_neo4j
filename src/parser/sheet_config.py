"""
Per-sheet parsing rules for the pumped-storage financial model.

Each config specifies:
  name_col     : column letter where the indicator name (Chinese) lives
  formula_col  : column letter of the first data-year formula (structural dependency)
  unit_col     : column letter for units (may be None)
  number_col   : column letter for section numbering (e.g. "一", "1", "1.1")
  header_rows  : row numbers (1-based) to skip as headers
  skip_patterns: substrings that mark rows to skip (e.g. yearly expansion rows)
  category_col : column that holds section/category headers (参数输入表 only)
  is_input     : True if this sheet contains manually-entered parameters
"""

SHEET_CONFIGS = {
    # ── 参数输入表 ──────────────────────────────────────────────────────────
    "参数输入表": {
        "name_col": "D",
        "formula_col": "I",
        "unit_col": "J",
        "number_col": "C",
        "category_col": "B",
        "header_rows": [1, 2, 3],
        # Rows that are yearly expansions of a parent indicator
        "skip_patterns": ["合作期第", "建设期第", "下拉菜单"],
        "is_input": True,
    },

    # ── 时间序列 ─────────────────────────────────────────────────────────────
    "时间序列": {
        "name_col": "B",
        "formula_col": "C",
        "unit_col": None,
        "number_col": None,
        "header_rows": [1, 2],
        "skip_patterns": [],
        "is_input": False,
    },

    # ── 投产&达产比例 ─────────────────────────────────────────────────────────
    "投产&达产比例": {
        "name_col": "C",
        "formula_col": "D",
        "unit_col": None,
        "number_col": "B",
        "header_rows": [1, 2, 3],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },

    # ── 投资概算明细 ──────────────────────────────────────────────────────────
    "投资概算明细": {
        "name_col": "C",
        "formula_col": "D",
        "unit_col": "B",
        "number_col": None,
        "header_rows": [1, 2, 3],
        "skip_patterns": [],
        "is_input": False,
    },

    # ── 表1-资金筹措及还本付息表 ───────────────────────────────────────────────
    "表1-资金筹措及还本付息表": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4, 5],
        "skip_patterns": ["合作期第", "建设期第", "第"],
        "is_input": False,
    },

    # ── 表2-折旧摊销表 ────────────────────────────────────────────────────────
    "表2-折旧摊销表": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },

    # ── 表3-成本费用表 ────────────────────────────────────────────────────────
    "表3-成本费用表": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },

    # ── 表4-收入税金表 ────────────────────────────────────────────────────────
    "表4-收入税金表": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },

    # ── 表5-利润表-资本金 ─────────────────────────────────────────────────────
    "表5-利润表-资本金": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },

    # ── 表6-现金流量表-资本金 ─────────────────────────────────────────────────
    "表6-现金流量表-资本金": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },

    # ── 表7-利润表-全投资 ─────────────────────────────────────────────────────
    "表7-利润表-全投资": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },

    # ── 表8-现金流量表-全投资 ─────────────────────────────────────────────────
    "表8-现金流量表-全投资": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },

    # ── 表9-现金流量表-财务计划 ───────────────────────────────────────────────
    "表9-现金流量表-财务计划": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },

    # ── 表10-资产负债表 ───────────────────────────────────────────────────────
    "表10-资产负债表": {
        "name_col": "C",
        "formula_col": "F",
        "unit_col": "E",
        "number_col": "B",
        "header_rows": [1, 2, 3, 4],
        "skip_patterns": ["合作期第", "建设期第"],
        "is_input": False,
    },
}

# High-level category mapping: sheet name → financial category
SHEET_CATEGORIES = {
    "参数输入表": "参数输入",
    "时间序列": "辅助计算",
    "投产&达产比例": "辅助计算",
    "投资概算明细": "投资估算",
    "表1-资金筹措及还本付息表": "融资与债务",
    "表2-折旧摊销表": "折旧摊销",
    "表3-成本费用表": "成本费用",
    "表4-收入税金表": "收入与税金",
    "表5-利润表-资本金": "利润表",
    "表6-现金流量表-资本金": "现金流量表",
    "表7-利润表-全投资": "利润表",
    "表8-现金流量表-全投资": "现金流量表",
    "表9-现金流量表-财务计划": "现金流量表",
    "表10-资产负债表": "资产负债表",
}

# Known circular dependency groups (for annotation)
CIRCULAR_GROUPS = [
    {
        "id": "circ_net_asset_tax",
        "description": "净资产税循环：表4净资产税预缴 ↔ 表9经营现金流 ↔ 表10净资产",
        "indicators": ["临时净资产税预缴", "临时净资产税计算", "经营活动现金流量净额", "净资产"],
    },
    {
        "id": "circ_irr_price",
        "description": "IRR反算电价循环：参数输入表IRR校验 ↔ 表6资本金IRR ↔ 容量电价",
        "indicators": ["资本金内部收益率", "容量电费单价", "资本金IRR校验"],
    },
]
