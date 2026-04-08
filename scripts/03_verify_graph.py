#!/usr/bin/env python3
"""
03_verify_graph.py

Step 3: Verify the Neo4j graph integrity after loading.

Run: python scripts/03_verify_graph.py
"""

import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src.graph.validator import GraphValidator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    with GraphValidator(config.NEO4J_URI, config.NEO4J_USER, config.NEO4J_PASSWORD) as v:
        v.print_report()


if __name__ == "__main__":
    main()
