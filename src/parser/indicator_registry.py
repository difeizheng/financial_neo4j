"""
indicator_registry.py

Extracts financial indicators from all sheets of the Excel model.
Each indicator becomes a node in the Neo4j graph.

Key challenge: the parameter sheet (参数输入表) has "yearly expansion" rows
where a single indicator (e.g. "年度达产率") is followed by 48 rows of
"合作期第N年" values. These must be collapsed into one indicator node.
"""

import re
import json
import logging
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string

from src.parser.sheet_config import SHEET_CONFIGS, SHEET_CATEGORIES
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Regex: detect yearly expansion rows like "合作期第1年", "建设期第3年"
_YEARLY_ROW_RE = re.compile(r"(合作期|建设期)第\d+[年月]?")

# Regex: detect pure numeric or section-number strings like "1", "1.1", "一", "（一）"
_SECTION_NUM_RE = re.compile(r"^[\d一二三四五六七八九十（）\.\s]+$")


def _col_idx(letter: str) -> int:
    """Convert column letter to 1-based index."""
    return column_index_from_string(letter)


def _cell_val(ws, row: int, col_letter: str):
    """Return stripped string value of a cell, or None."""
    if col_letter is None:
        return None
    val = ws.cell(row=row, column=_col_idx(col_letter)).value
    if val is None:
        return None
    return str(val).strip()


def _is_yearly_expansion(name: str) -> bool:
    return bool(_YEARLY_ROW_RE.search(name))


def _is_meaningful_name(name: str) -> bool:
    """Return True if the string looks like a real indicator name (not a header/number)."""
    if not name:
        return False
    if _YEARLY_ROW_RE.search(name):
        return False
    if _SECTION_NUM_RE.match(name):
        return False
    # Must contain at least one Chinese character
    if not re.search(r"[\u4e00-\u9fff]", name):
        return False
    return True


def _make_id(sheet_name: str, name: str, row: int) -> str:
    """Create a unique indicator ID."""
    # Sanitize: remove special chars
    safe_sheet = re.sub(r"[^\w\u4e00-\u9fff]", "_", sheet_name)
    safe_name = re.sub(r"[^\w\u4e00-\u9fff]", "_", name)
    return f"{safe_sheet}__{safe_name}__{row}"


def extract_indicators(
    excel_path: Path,
    sheet_configs: Optional[dict] = None,
    sheet_categories: Optional[dict] = None,
    progress_callback: Optional[Callable[[str, float], None]] = None,
) -> list[dict]:
    """
    Parse the Excel file and return a list of indicator dicts.

    sheet_configs: if None, uses the hardcoded SHEET_CONFIGS (CLI path).
    sheet_categories: if None, uses the hardcoded SHEET_CATEGORIES.
    progress_callback: optional callable(message, percent) for progress reporting.
    """
    configs = sheet_configs if sheet_configs is not None else SHEET_CONFIGS
    categories = sheet_categories if sheet_categories is not None else SHEET_CATEGORIES

    logger.info(f"Loading workbook (formulas): {excel_path}")
    wb = load_workbook(excel_path, data_only=False, read_only=True)

    all_indicators = []
    sheet_names = list(configs.keys())
    total = len(sheet_names)

    for i, sheet_name in enumerate(sheet_names):
        if sheet_name not in wb.sheetnames:
            logger.warning(f"Sheet not found: {sheet_name!r}")
            continue

        ws = wb[sheet_name]
        logger.info(f"Processing sheet: {sheet_name}")
        if progress_callback:
            progress_callback(f"解析工作表: {sheet_name}", i / total)

        cfg = configs[sheet_name]
        indicators = _extract_from_sheet(ws, sheet_name, cfg, categories)
        logger.info(f"  → {len(indicators)} indicators extracted")
        all_indicators.extend(indicators)

    wb.close()
    logger.info(f"Total indicators extracted: {len(all_indicators)}")
    return all_indicators


def _extract_from_sheet(ws, sheet_name: str, cfg: dict, sheet_categories: dict) -> list[dict]:
    """Extract indicators from a single sheet."""
    name_col = cfg["name_col"]
    formula_col = cfg["formula_col"]
    unit_col = cfg.get("unit_col")
    number_col = cfg.get("number_col")
    header_rows = set(cfg.get("header_rows", []))
    skip_patterns = cfg.get("skip_patterns", [])
    is_input = cfg.get("is_input", False)
    sheet_category = sheet_categories.get(sheet_name, "其他")

    indicators = []
    current_category = ""  # tracks section headers in 参数输入表

    max_row = ws.max_row or 0

    for row_num in range(1, max_row + 1):
        if row_num in header_rows:
            continue

        name = _cell_val(ws, row_num, name_col)
        if not name:
            continue

        # Skip yearly expansion rows
        if any(pat in name for pat in skip_patterns):
            continue

        # Track category headers (参数输入表 uses column B for section headers)
        if cfg.get("category_col"):
            cat_val = _cell_val(ws, row_num, cfg["category_col"])
            if cat_val and re.search(r"[\u4e00-\u9fff]", cat_val) and len(cat_val) < 20:
                current_category = cat_val

        if not _is_meaningful_name(name):
            continue

        # Get formula / value
        formula_raw = _cell_val(ws, row_num, formula_col) if formula_col else None

        # Get unit
        unit = _cell_val(ws, row_num, unit_col) if unit_col else None

        # Get section number
        section_number = _cell_val(ws, row_num, number_col) if number_col else None

        indicator = {
            "id": _make_id(sheet_name, name, row_num),
            "name": name,
            "sheet": sheet_name,
            "sheet_category": sheet_category,
            "category": current_category or sheet_category,
            "row": row_num,
            "formula_raw": formula_raw,
            "unit": unit or "万元",
            "section_number": section_number,
            "is_input": is_input,
            "is_circular": False,  # will be updated by formula_parser
        }
        indicators.append(indicator)

    return indicators


def save_indicators(indicators: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(indicators, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved {len(indicators)} indicators to {output_path}")


def load_indicators(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
