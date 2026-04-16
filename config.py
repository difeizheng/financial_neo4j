import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# Excel
EXCEL_FILE = BASE_DIR / os.getenv(
    "EXCEL_FILE",
    "数字化系统财务模型边界【抽水蓄能】v15(亏损弥补+分红预提税+净资产税+折旧摊销优化）.xlsx"
)

# Neo4j
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")

# LLM — supports OpenAI-compatible APIs and Anthropic
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai")  # "openai" | "anthropic"
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")

# Data output
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
INDICATORS_FILE = DATA_DIR / "indicators.json"
DEPENDENCIES_FILE = DATA_DIR / "dependencies.json"
CHILD_RELATIONSHIPS_FILE = DATA_DIR / "child_relationships.json"

# Multi-task data directory
TASKS_DIR = BASE_DIR / "tasks"
TASKS_DIR.mkdir(exist_ok=True)

# Chat history SQLite DB
CHAT_DB = TASKS_DIR / "chat_history.db"

# Trial (试算) SQLite DB
TRIALS_DB = TASKS_DIR / "trials.db"
