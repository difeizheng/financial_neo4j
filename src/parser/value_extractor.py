"""
value_extractor.py

Loads the Excel workbook in data_only mode to extract computed values.
Enriches each indicator with:
  - value_year1: first operational year value (column F, or first non-zero)
  - values_json: full time series as JSON string (columns F through end)
"""

import json
import logging
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.utils import column_index_from_string, get_column_letter

from src.parser.sheet_config import SHEET_CONFIGS

logger = logging.getLogger(__name__)

# First data column (year 1 of cooperation period) is typically column F
_DEFAULT_FIRST_DATA_COL = "F"
# Max number of yearly columns to extract (48 years + some buffer)
_MAX_YEARS = 55


def _col_idx(letter: str) -> int:
    return column_index_from_string(letter)


def extract_values(excel_path: Path, indicators: list[dict]) -> list[dict]:
    """
    For each indicator, extract its computed value(s) from the Excel.
    Modifies indicators in-place and returns them.
    """
    logger.info(f"Loading workbook (data_only): {excel_path}")
    wb = load_workbook(excel_path, data_only=True, read_only=True)

    # Build lookup: (sheet, row) -> indicator index in list
    lookup = {}
    for i, ind in enumerate(indicators):
        lookup[(ind["sheet"], ind["row"])] = i

    for sheet_name, cfg in SHEET_CONFIGS.items():
        if sheet_name not in wb.sheetnames:
            continue

        ws = wb[sheet_name]
        formula_col = cfg.get("formula_col") or _DEFAULT_FIRST_DATA_COL
        first_col_idx = _col_idx(formula_col)

        for row_num in range(1, (ws.max_row or 0) + 1):
            key = (sheet_name, row_num)
            if key not in lookup:
                continue

            idx = lookup[key]
            # Extract time series: from formula_col up to _MAX_YEARS columns
            values = []
            for col_offset in range(_MAX_YEARS):
                col_idx = first_col_idx + col_offset
                cell_val = ws.cell(row=row_num, column=col_idx).value
                if cell_val is None:
                    values.append(None)
                else:
                    try:
                        values.append(float(cell_val))
                    except (TypeError, ValueError):
                        values.append(str(cell_val))

            # Find first non-None, non-zero value as representative
            value_year1 = None
            for v in values:
                if v is not None and v != 0 and v != "":
                    value_year1 = v
                    break

            indicators[idx]["value_year1"] = value_year1
            indicators[idx]["values_json"] = json.dumps(values, ensure_ascii=False)

    wb.close()
    logger.info("Value extraction complete")
    return indicators
