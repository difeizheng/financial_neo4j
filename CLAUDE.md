# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a financial knowledge graph system that converts a pumped-storage hydropower Excel financial model (14 sheets, 48-year timeline, 1400MW capacity) into a Neo4j graph database with an LLM-powered conversational interface.

**Core concept**: Financial statements form a directed cyclic graph where indicators depend on each other through formulas. The system extracts these dependencies from Excel, stores them in Neo4j for traceability, and uses an LLM to answer natural language questions about the financial model.

## Pipeline Execution Order

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment (copy .env.example to .env and fill in credentials)
cp .env.example .env

# 3. Parse Excel → JSON
python scripts/01_parse_excel.py

# 4. Load JSON → Neo4j
python scripts/02_load_neo4j.py

# 5. Verify graph integrity
python scripts/03_verify_graph.py

# 6. Launch conversational interface
python scripts/04_chat.py
```

## Architecture

### Three-Layer Design

1. **Parser Layer** (`src/parser/`)
   - Extracts ~300-400 financial indicators from Excel sheets
   - Parses formula dependencies using regex on cell references
   - Handles "yearly expansion rows" (参数输入表 has indicators followed by 48 yearly value rows that must be collapsed into single nodes)
   - Outputs: `data/indicators.json`, `data/dependencies.json`

2. **Graph Layer** (`src/graph/`)
   - Loads indicators and dependencies into Neo4j
   - Schema: `Indicator` nodes with `DEPENDS_ON` edges
   - Batch operations via UNWIND for performance
   - Validates graph integrity (node counts, circular paths, key financial statement relationships)

3. **LLM Layer** (`src/llm/`)
   - Text-to-Cypher: converts natural language questions to Neo4j queries
   - Supports both OpenAI-compatible APIs (via `openai` library) and Anthropic Claude
   - System prompt includes graph schema, example queries, and financial domain knowledge
   - Interprets query results in Chinese with financial expertise

### Data Flow

```
Excel (14 sheets)
  → indicator_registry.py: extract indicator names, formulas, row positions
  → formula_parser.py: parse formulas → dependency edges
  → value_extractor.py: extract computed values (data_only mode)
  → loader.py: batch load to Neo4j
  → cypher_generator.py: LLM generates Cypher from user questions
  → Neo4j query results → LLM interprets → Chinese answer
```

### Critical Configuration: `sheet_config.py`

Each of the 14 Excel sheets has different column layouts. `SHEET_CONFIGS` defines per-sheet parsing rules:
- `name_col`: where indicator names live (e.g., "C" for most sheets, "D" for 参数输入表)
- `formula_col`: where formulas start (e.g., "F" for calculation sheets, "I" for 参数输入表)
- `header_rows`: rows to skip as headers
- `skip_patterns`: substrings to skip (e.g., "合作期第" marks yearly expansion rows)

**Most common parsing issue**: If indicator extraction fails, check if the Excel has unexpected empty rows or merged cells. Adjust `header_rows` or `skip_patterns` in `sheet_config.py`.

### Circular Dependencies

The model has two known circular dependency groups (Excel uses iterative calculation):
1. **Net asset tax loop**: 表4净资产税预缴 ↔ 表9经营现金流 ↔ 表10净资产
2. **IRR-price loop**: 参数输入表IRR校验 ↔ 表6资本金IRR ↔ 容量电价

These are annotated in `CIRCULAR_GROUPS` and marked with `is_circular: true` on nodes/edges.

## Neo4j Graph Schema

**Nodes**:
- `Indicator`: financial metrics (name, sheet, formula_raw, value_year1, values_json, is_input, is_circular)
- `Sheet`: Excel worksheets
- `Category`: financial categories

**Relationships**:
- `DEPENDS_ON`: Indicator A's calculation depends on Indicator B (properties: operation, is_cross_sheet, is_circular)
- `BELONGS_TO`: Indicator → Sheet
- `FEEDS_INTO`: Sheet → Sheet (data flow)
- `IN_CATEGORY`: Indicator → Category

## LLM Configuration

The system supports multiple LLM providers via `.env`:

```bash
# OpenAI-compatible (OpenAI, DeepSeek, Qwen, local Ollama, etc.)
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o

# Or Anthropic Claude
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6
```

The `cypher_generator.py` abstracts provider differences with a unified callable interface.

## Debugging Tips

**Step 1 (parsing) issues**:
- Open `data/indicators.json` and spot-check 5-10 indicators
- Verify names match Excel, formulas are captured
- If too many/few indicators, adjust `skip_patterns` in `sheet_config.py`

**Step 2 (loading) issues**:
- Check Neo4j is running: `bolt://localhost:7687`
- Verify credentials in `.env`

**Step 3 (validation) issues**:
- Expected: 200-500 Indicator nodes, 300-800 DEPENDS_ON edges
- If orphan count is high, formula parsing may have failed (check regex in `formula_parser.py`)
- Circular paths should exist (validates known loops)

**Step 4 (chat) issues**:
- If Cypher generation fails, check LLM API key and model name
- If queries return empty results, indicator names may not match (use `CONTAINS` for fuzzy matching)
- The system prompt in `prompts.py` includes example Cypher patterns — add more if needed

## Key Files to Modify

- **Add new Excel sheets**: Update `SHEET_CONFIGS` in `src/parser/sheet_config.py`
- **Change graph schema**: Update `src/graph/schema.py` and `src/graph/loader.py`
- **Improve Cypher generation**: Edit system prompt in `src/llm/prompts.py`
- **Add new LLM provider**: Extend `_make_llm_client()` in `src/llm/cypher_generator.py`
