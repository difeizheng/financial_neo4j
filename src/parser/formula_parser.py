"""
formula_parser.py

Parses Excel formula strings to extract inter-indicator dependencies.
Produces a list of edges: {source_id, target_id, operation, formula_fragment, is_cross_sheet}

Strategy:
  1. Regex-extract all cell references from each indicator's formula_raw
  2. Map cell references (sheet + row) back to indicator IDs via a row-lookup index
  3. Classify the operation type from the formula structure
  4. Annotate known circular dependency groups
"""

import re
import json
import logging
from pathlib import Path

from src.parser.sheet_config import CIRCULAR_GROUPS
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cell reference patterns ────────────────────────────────────────────────────
# Matches: 'SheetName'!$A$1  or  SheetName!A1  or  $A$1  or  A1
_CROSS_SHEET_REF = re.compile(
    r"'?([^'!\[\]]+)'?![$]?([A-Za-z]+)[$]?(\d+)"
)
_SAME_SHEET_REF = re.compile(
    r"(?<![!'\w])[$]?([A-Za-z]{1,3})[$]?(\d+)(?!\d)"
)

# ── Operation type detection ───────────────────────────────────────────────────
def _detect_operation(formula: str) -> str:
    if not formula or not formula.startswith("="):
        return "value"
    f = formula.upper()
    if "XIRR" in f:
        return "irr"
    if "IF(" in f:
        return "conditional"
    if "SUM(" in f or "SUMIF(" in f:
        return "sum"
    if "VLOOKUP(" in f or "INDEX(" in f or "MATCH(" in f:
        return "lookup"
    if "*" in f and "/" not in f:
        return "multiply"
    if "/" in f:
        return "divide"
    if "-" in f:
        return "subtract"
    if "+" in f:
        return "add"
    return "reference"


def build_row_index(indicators: list[dict]) -> dict:
    """
    Build a lookup: (sheet_name, row_number) -> indicator_id
    Used to resolve cell references back to indicator IDs.
    """
    index = {}
    for ind in indicators:
        key = (ind["sheet"], ind["row"])
        index[key] = ind["id"]
    return index


def _normalize_sheet_name(raw: str) -> str:
    """Strip quotes and whitespace from sheet name extracted by regex."""
    return raw.strip().strip("'\"")


def parse_dependencies(
    indicators: list[dict],
    circular_groups: Optional[list] = None,
) -> list[dict]:
    """
    For each indicator with a formula, extract dependencies on other indicators.
    Returns a list of edge dicts.

    circular_groups: if None, uses the hardcoded CIRCULAR_GROUPS (CLI path).
    """
    groups = circular_groups if circular_groups is not None else CIRCULAR_GROUPS
    row_index = build_row_index(indicators)

    # Also build a name->id index for circular group annotation
    name_index = {ind["name"]: ind["id"] for ind in indicators}

    # Build sheet-name normalization map (actual sheet names from indicators)
    known_sheets = {ind["sheet"] for ind in indicators}

    edges = []
    seen_edges = set()

    for ind in indicators:
        formula = ind.get("formula_raw") or ""
        if not formula or not formula.startswith("="):
            continue

        source_id = ind["id"]
        source_sheet = ind["sheet"]
        operation = _detect_operation(formula)

        # 1. Extract cross-sheet references
        for match in _CROSS_SHEET_REF.finditer(formula):
            raw_sheet, col_letter, row_str = match.groups()
            sheet_name = _normalize_sheet_name(raw_sheet)
            row_num = int(row_str)

            # Try to find the target indicator
            target_id = row_index.get((sheet_name, row_num))
            if target_id is None:
                # Try fuzzy match on sheet name (partial match)
                for known in known_sheets:
                    if sheet_name in known or known in sheet_name:
                        target_id = row_index.get((known, row_num))
                        if target_id:
                            break

            if target_id and target_id != source_id:
                edge_key = (source_id, target_id)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source_id": source_id,
                        "target_id": target_id,
                        "operation": operation,
                        "formula_fragment": match.group(0),
                        "is_cross_sheet": True,
                        "is_circular": False,
                    })

        # 2. Extract same-sheet references (after removing cross-sheet refs)
        formula_no_cross = _CROSS_SHEET_REF.sub("__REMOVED__", formula)
        for match in _SAME_SHEET_REF.finditer(formula_no_cross):
            col_letter, row_str = match.groups()
            row_num = int(row_str)

            target_id = row_index.get((source_sheet, row_num))
            if target_id and target_id != source_id:
                edge_key = (source_id, target_id)
                if edge_key not in seen_edges:
                    seen_edges.add(edge_key)
                    edges.append({
                        "source_id": source_id,
                        "target_id": target_id,
                        "operation": operation,
                        "formula_fragment": match.group(0),
                        "is_cross_sheet": False,
                        "is_circular": False,
                    })

    # 3. Annotate known circular groups
    edges = _annotate_circular(edges, name_index, indicators, groups)

    logger.info(f"Extracted {len(edges)} dependency edges")
    return edges


def _annotate_circular(
    edges: list[dict],
    name_index: dict,
    indicators: list[dict],
    groups: list,
) -> list[dict]:
    """Mark edges and indicators that belong to known circular dependency groups."""
    circular_indicator_ids = set()

    for group in groups:
        group_ids = set()
        for name in group["indicators"]:
            if name in name_index:
                group_ids.add(name_index[name])

        # Mark edges between group members as circular
        for edge in edges:
            if edge["source_id"] in group_ids and edge["target_id"] in group_ids:
                edge["is_circular"] = True
                edge["circular_group"] = group["id"]

        circular_indicator_ids.update(group_ids)

    # Update indicator is_circular flag
    for ind in indicators:
        if ind["id"] in circular_indicator_ids:
            ind["is_circular"] = True

    return edges


def save_dependencies(edges: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(edges, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved {len(edges)} edges to {output_path}")


def load_dependencies(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
