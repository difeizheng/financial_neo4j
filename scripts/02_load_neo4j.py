#!/usr/bin/env python3
"""
02_load_neo4j.py

Step 2: Load parsed indicators and dependencies into Neo4j.

Prerequisites:
  - Neo4j running (see .env for connection settings)
  - data/indicators.json, data/dependencies.json, and data/child_relationships.json exist
    (run 01_parse_excel.py first)

Run: python scripts/02_load_neo4j.py
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src.parser.indicator_registry import load_indicators, load_child_of_edges
from src.parser.formula_parser import load_dependencies
from src.graph.loader import GraphLoader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    if not config.INDICATORS_FILE.exists():
        logger.error(f"indicators.json not found. Run 01_parse_excel.py first.")
        sys.exit(1)
    if not config.DEPENDENCIES_FILE.exists():
        logger.error(f"dependencies.json not found. Run 01_parse_excel.py first.")
        sys.exit(1)

    logger.info("Loading parsed data...")
    indicators = load_indicators(config.INDICATORS_FILE)
    edges = load_dependencies(config.DEPENDENCIES_FILE)
    child_edges = []
    if config.CHILD_RELATIONSHIPS_FILE.exists():
        child_edges = load_child_of_edges(config.CHILD_RELATIONSHIPS_FILE)
    logger.info(f"  {len(indicators)} indicators, {len(edges)} deps, {len(child_edges)} child edges")

    logger.info(f"Connecting to Neo4j at {config.NEO4J_URI}...")
    with GraphLoader(config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASSWORD) as loader:
        loader.load_all(indicators, edges, child_edges)

    logger.info("Done. Run 03_verify_graph.py to validate the graph.")


if __name__ == "__main__":
    main()
