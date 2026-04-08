#!/usr/bin/env python3
"""
01_parse_excel.py

Step 1: Parse the Excel financial model and output:
  - data/indicators.json  (financial indicator nodes)
  - data/dependencies.json (dependency edges)

Run: python scripts/01_parse_excel.py
"""

import sys
import logging
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src.parser.indicator_registry import extract_indicators, save_indicators
from src.parser.formula_parser import parse_dependencies, save_dependencies
from src.parser.value_extractor import extract_values

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    excel_path = config.EXCEL_FILE
    if not excel_path.exists():
        logger.error(f"Excel file not found: {excel_path}")
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Step 1: Extracting indicators from Excel...")
    indicators = extract_indicators(excel_path)
    logger.info(f"  Extracted {len(indicators)} indicators")

    logger.info("Step 2: Extracting computed values...")
    indicators = extract_values(excel_path, indicators)

    logger.info("Step 3: Parsing formula dependencies...")
    edges = parse_dependencies(indicators)
    logger.info(f"  Extracted {len(edges)} dependency edges")

    logger.info("Step 4: Saving to JSON...")
    save_indicators(indicators, config.INDICATORS_FILE)
    save_dependencies(edges, config.DEPENDENCIES_FILE)

    # Quick summary
    circular_inds = sum(1 for i in indicators if i.get("is_circular"))
    circular_edges = sum(1 for e in edges if e.get("is_circular"))
    cross_sheet_edges = sum(1 for e in edges if e.get("is_cross_sheet"))

    logger.info("=" * 60)
    logger.info("Summary:")
    logger.info(f"  Indicators:          {len(indicators)}")
    logger.info(f"  Dependency edges:    {len(edges)}")
    logger.info(f"  Cross-sheet edges:   {cross_sheet_edges}")
    logger.info(f"  Circular indicators: {circular_inds}")
    logger.info(f"  Circular edges:      {circular_edges}")
    logger.info(f"  Output: {config.INDICATORS_FILE}")
    logger.info(f"  Output: {config.DEPENDENCIES_FILE}")
    logger.info("=" * 60)
    logger.info("Done. Review data/indicators.json before loading to Neo4j.")


if __name__ == "__main__":
    main()
