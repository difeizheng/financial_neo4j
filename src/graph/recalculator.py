"""
recalculator.py

Cascading recalculation engine using the `formulas` library.
Loads the Excel workbook as a calculation model, applies parameter changes,
recalculates, and returns new values for all indicators.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Number of years in the financial model
NUM_YEARS = 48


class ParameterRecalculator:
    """
    Uses the `formulas` library to recalculate an Excel financial model
    after parameter overrides are applied.

    Usage:
        recalc = ParameterRecalculator(
            task_id="abc123",
            excel_path=Path("tasks/abc123/uploaded.xlsx"),
            indicators=indicators,          # list of dicts from indicators.json
            sheet_configs=sheet_configs,    # dict from config.json["sheet_configs"]
        )
        new_values = recalc.recalculate(changes={"运营期": 30.0})
        # new_values: {"Indicator__id": [val_y1, ..., val_y48], ...}
    """

    def __init__(
        self,
        task_id: str,
        excel_path: Path,
        indicators: list[dict],
        sheet_configs: dict,
        model_cache: Optional[dict] = None,
    ):
        self.task_id = task_id
        self.excel_path = excel_path
        self.sheet_configs = sheet_configs
        # formulas cell key prefix: "'[filename]sheetname'!CELLREF"
        self._excel_filename = Path(excel_path).name

        # Build id → indicator lookup
        self._id_to_ind: dict[str, dict] = {ind["id"]: ind for ind in indicators}

        # model_cache: external dict {excel_path_str: model} for cross-call reuse
        self._model_cache = model_cache if model_cache is not None else {}
        self._model: Optional[object] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def recalculate(
        self,
        changes: dict[str, float | int],
        progress_callback=None,
    ) -> dict[str, list]:
        """
        Apply parameter changes to the Excel model and recalculate.

        Args:
            changes:    {indicator_id: new_value}  — single values only for now
            progress_callback: callable(msg: str) for progress updates

        Returns:
            {indicator_id: [val_y1, ..., val_y48]}

        Raises:
            RuntimeError if the formulas library fails to load or calculate.
        """
        if not changes:
            return self._extract_all_values(progress_callback)

        progress_callback and progress_callback("加载 Excel 计算模型...")
        model = self._load_model()

        progress_callback and progress_callback("应用参数修改...")
        for ind_id, new_val in changes.items():
            self._set_cell_value(model, ind_id, new_val)

        progress_callback and progress_callback("正在重算 (formulas 引擎处理循环依赖迭代)...")
        try:
            model.calculate()
        except Exception as e:
            logger.warning(f"formulas.calculate() raised: {e} — trying partial recalc")
            try:
                # Try calculating just the affected subgraph
                self._calculate_affected(model, changes)
            except Exception as e2:
                raise RuntimeError(f"重算失败: {e2}") from e

        progress_callback and progress_callback("提取计算结果...")
        results = self._extract_all_values(progress_callback)
        return results

    def get_editable_params(self, indicators: list[dict]) -> list[dict]:
        """Return indicators that are editable parameters (is_input=True, no formula)."""
        return [
            ind
            for ind in indicators
            if ind.get("is_input")
            and not str(ind.get("formula_raw") or "").startswith("=")
        ]

    # ── Model loading ──────────────────────────────────────────────────────────

    def _load_model(self):
        """Load (or return cached) ExcelModel from the formulas library."""
        cache_key = str(self.excel_path)

        # Check external cache first (survives across ParameterRecalculator instances)
        if cache_key in self._model_cache:
            self._model = self._model_cache[cache_key]
            return self._model

        # Check instance cache
        if self._model is not None:
            return self._model

        import formulas

        self._model = (
            formulas.ExcelModel()
            .loads(str(self.excel_path))
            .finish()
        )
        logger.info(f"Excel model loaded: {self.excel_path}")

        # Store in external cache for reuse
        self._model_cache[cache_key] = self._model
        return self._model

    # ── Cell manipulation ──────────────────────────────────────────────────────

    def _cell_key(self, sheet: str, cell_ref: str) -> str:
        """Return the formulas-library cell key: \"'[filename]sheet'!CELLREF\"."""
        return f"'[{self._excel_filename}]{sheet}'!{cell_ref}"

    def _set_cell_value(self, model, ind_id: str, new_val: float | int):
        """Set a parameter cell value in the model."""
        ind = self._id_to_ind.get(ind_id)
        if not ind:
            logger.warning(f"Indicator not found for cell set: {ind_id}")
            return

        sheet = ind["sheet"]
        row = ind["row"]
        start_col = self.sheet_configs.get(sheet, {}).get("formula_col", "I")

        cell_ref = self._cell_ref(sheet, row, start_col, year=0)
        key = self._cell_key(sheet, cell_ref)

        try:
            cell = model.cells.get(key)
            if cell is None:
                logger.error(f"Cell not found in model: {key}")
                return
            cell.value = float(new_val)
            logger.debug(f"Set {key} = {new_val}")
        except Exception as e:
            logger.error(f"Failed to set cell {key}: {e}")

    def _cell_ref(self, sheet: str, row: int, start_col: str, year: int) -> str:
        """Return Excel cell reference like 'I5' for year=0 or 'J5' for year=1."""
        import openpyxl.utils

        col_idx = openpyxl.utils.column_index_from_string(start_col) + year
        col_letter = openpyxl.utils.get_column_letter(col_idx)
        return f"{col_letter}{row}"

    def _get_cell_value(self, model, sheet: str, row: int, start_col: str, year: int):
        """Read a cell value from the model."""
        cell_ref = self._cell_ref(sheet, row, start_col, year)
        key = self._cell_key(sheet, cell_ref)
        try:
            cell = model.cells.get(key)
            if cell is None:
                return None
            val = cell.value
            # Unwrap numpy types or formulas.Double objects
            if hasattr(val, "value"):
                val = val.value
            return float(val) if val is not None else None
        except Exception:
            return None

    # ── Value extraction ────────────────────────────────────────────────────────

    def _extract_all_values(self, progress_callback=None) -> dict[str, list]:
        """Extract 48-year values for all indicators from the current model state."""
        model = self._load_model()
        results = {}
        indicators = list(self._id_to_ind.values())
        total = len(indicators)

        for i, ind in enumerate(indicators):
            if progress_callback and i % 100 == 0:
                pct = int(i / total * 100)
                progress_callback(f"提取值进度 {pct}% ({i}/{total})...")

            ind_id = ind["id"]
            sheet = ind["sheet"]
            row = ind["row"]
            start_col = self.sheet_configs.get(sheet, {}).get("formula_col", "I")

            values = []
            for year in range(NUM_YEARS):
                val = self._get_cell_value(model, sheet, row, start_col, year)
                values.append(val)

            results[ind_id] = values

        return results

    def _calculate_affected(self, model, changes: dict):
        """
        Fallback: calculate only affected indicators when full recalc fails.
        """
        try:
            keys = []
            for ind_id in changes:
                ind = self._id_to_ind.get(ind_id)
                if ind:
                    cell_ref = self._cell_ref(
                        ind["sheet"], ind["row"],
                        self.sheet_configs.get(ind["sheet"], {}).get("formula_col", "I"),
                        0,
                    )
                    keys.append(self._cell_key(ind["sheet"], cell_ref))
            model.calculate(deps=keys)
        except Exception:
            model.calculate()
