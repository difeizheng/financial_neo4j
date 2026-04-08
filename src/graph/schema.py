"""
schema.py

Neo4j graph schema: constraints, indexes, and node/relationship definitions.

Node labels:
  Indicator  — a financial metric/indicator
  Sheet      — an Excel worksheet
  Category   — high-level financial category

Relationship types:
  DEPENDS_ON   — Indicator depends on another Indicator (directed)
  BELONGS_TO   — Indicator belongs to a Sheet
  FEEDS_INTO   — Sheet feeds data into another Sheet
  IN_CATEGORY  — Indicator belongs to a Category
"""

CONSTRAINTS_AND_INDEXES = [
    # Uniqueness constraints
    "CREATE CONSTRAINT indicator_id IF NOT EXISTS FOR (n:Indicator) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT sheet_name IF NOT EXISTS FOR (n:Sheet) REQUIRE n.name IS UNIQUE",
    "CREATE CONSTRAINT category_name IF NOT EXISTS FOR (n:Category) REQUIRE n.name IS UNIQUE",
    # Indexes for common lookups
    "CREATE INDEX indicator_name IF NOT EXISTS FOR (n:Indicator) ON (n.name)",
    "CREATE INDEX indicator_sheet IF NOT EXISTS FOR (n:Indicator) ON (n.sheet)",
    "CREATE INDEX indicator_category IF NOT EXISTS FOR (n:Indicator) ON (n.category)",
    "CREATE INDEX indicator_is_input IF NOT EXISTS FOR (n:Indicator) ON (n.is_input)",
    "CREATE INDEX indicator_is_circular IF NOT EXISTS FOR (n:Indicator) ON (n.is_circular)",
]

# Sheet-level feed-into relationships (manually defined from known data flow)
SHEET_FEED_INTO = [
    ("参数输入表", "投产&达产比例"),
    ("参数输入表", "投资概算明细"),
    ("参数输入表", "表1-资金筹措及还本付息表"),
    ("参数输入表", "表2-折旧摊销表"),
    ("参数输入表", "表3-成本费用表"),
    ("参数输入表", "表4-收入税金表"),
    ("参数输入表", "表5-利润表-资本金"),
    ("参数输入表", "表6-现金流量表-资本金"),
    ("参数输入表", "表7-利润表-全投资"),
    ("参数输入表", "表8-现金流量表-全投资"),
    ("参数输入表", "表9-现金流量表-财务计划"),
    ("参数输入表", "表10-资产负债表"),
    ("投产&达产比例", "参数输入表"),  # feeds back annual ramp-up rates
    ("投资概算明细", "表1-资金筹措及还本付息表"),
    ("表1-资金筹措及还本付息表", "表2-折旧摊销表"),
    ("表1-资金筹措及还本付息表", "表3-成本费用表"),
    ("表1-资金筹措及还本付息表", "表6-现金流量表-资本金"),
    ("表1-资金筹措及还本付息表", "表9-现金流量表-财务计划"),
    ("表1-资金筹措及还本付息表", "表10-资产负债表"),
    ("表2-折旧摊销表", "表3-成本费用表"),
    ("表2-折旧摊销表", "表10-资产负债表"),
    ("表3-成本费用表", "表4-收入税金表"),
    ("表3-成本费用表", "表5-利润表-资本金"),
    ("表3-成本费用表", "表7-利润表-全投资"),
    ("表4-收入税金表", "表5-利润表-资本金"),
    ("表4-收入税金表", "表7-利润表-全投资"),
    ("表5-利润表-资本金", "表6-现金流量表-资本金"),
    ("表5-利润表-资本金", "表9-现金流量表-财务计划"),
    ("表5-利润表-资本金", "表10-资产负债表"),
    ("表6-现金流量表-资本金", "表9-现金流量表-财务计划"),
    ("表7-利润表-全投资", "表8-现金流量表-全投资"),
    ("表9-现金流量表-财务计划", "表10-资产负债表"),
    # Circular: 表4 ↔ 表9 ↔ 表10
    ("表9-现金流量表-财务计划", "表4-收入税金表"),
    ("表10-资产负债表", "表4-收入税金表"),
]

SHEET_DESCRIPTIONS = {
    "参数输入表": "所有参数的输入中枢，包含工程计划、技术参数、投资概算、融资方案、成本、收入、税收政策",
    "时间序列": "项目全周期时间轴映射（建设期+运营期）",
    "投产&达产比例": "月度粒度的投产比例和达产率，汇总为年度数据反馈至参数输入表",
    "投资概算明细": "分年度投资分配明细及增值税进项税额计算",
    "表1-资金筹措及还本付息表": "资金来源（资本金+债务）及长期贷款、流动资金贷款还本付息计划",
    "表2-折旧摊销表": "固定资产折旧、无形资产摊销、长期待摊费用摊销、大修及更新重置折旧",
    "表3-成本费用表": "生产成本、期间费用（销售/管理/研发/财务）、现金支出成本",
    "表4-收入税金表": "容量电费和电量电费收入、增值税计算（含进项税抵扣）、附加税、净资产税",
    "表5-利润表-资本金": "资本金口径利润表，含亏损弥补（10年限制）、三免三减半所得税、股息红利预提税",
    "表6-现金流量表-资本金": "资本金口径现金流量表，计算资本金内部收益率（XIRR）",
    "表7-利润表-全投资": "全投资口径利润表，不含财务费用，含临时净资产税",
    "表8-现金流量表-全投资": "全投资口径现金流量表，计算全投资内部收益率（XIRR）",
    "表9-现金流量表-财务计划": "三活动现金流量表（经营/投资/筹资），含短期借款需求计算",
    "表10-资产负债表": "标准资产负债表，含资产=负债+权益的勾稽校验",
}
