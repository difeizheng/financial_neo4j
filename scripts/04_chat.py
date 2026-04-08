#!/usr/bin/env python3
"""
04_chat.py

Step 4: Launch the conversational Q&A interface.

Prerequisites:
  - Neo4j running with data loaded (run 01, 02, 03 first)
  - LLM API key configured in .env

Run: python scripts/04_chat.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.app import main

if __name__ == "__main__":
    main()
