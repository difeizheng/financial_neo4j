"""
loader.py

Loads indicators and dependencies into Neo4j.
Uses UNWIND for batch operations to maximize performance.
"""

import logging
from neo4j import GraphDatabase

from src.graph.schema import (
    CONSTRAINTS_AND_INDEXES,
    SHEET_FEED_INTO,
    SHEET_DESCRIPTIONS,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


class GraphLoader:
    def __init__(self, uri: str, user: str, password: str):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _run(self, query: str, **params):
        with self.driver.session() as session:
            return session.run(query, **params)

    # ── Schema setup ──────────────────────────────────────────────────────────

    def setup_schema(self):
        logger.info("Creating constraints and indexes...")
        for stmt in CONSTRAINTS_AND_INDEXES:
            try:
                self._run(stmt)
            except Exception as e:
                logger.warning(f"Schema stmt skipped ({e}): {stmt[:60]}")

    # ── Nodes ─────────────────────────────────────────────────────────────────

    def load_indicators(self, indicators: list[dict]):
        logger.info(f"Loading {len(indicators)} Indicator nodes...")
        for i in range(0, len(indicators), _BATCH_SIZE):
            batch = indicators[i : i + _BATCH_SIZE]
            self._run(
                """
                UNWIND $batch AS row
                MERGE (n:Indicator {id: row.id})
                SET n.name          = row.name,
                    n.sheet         = row.sheet,
                    n.sheet_category = row.sheet_category,
                    n.category      = row.category,
                    n.row           = row.row,
                    n.formula_raw   = row.formula_raw,
                    n.unit          = row.unit,
                    n.section_number = row.section_number,
                    n.is_input      = row.is_input,
                    n.is_circular   = row.is_circular,
                    n.value_year1   = row.value_year1,
                    n.values_json   = row.values_json
                """,
                batch=batch,
            )
        logger.info("Indicator nodes loaded.")

    def load_sheets(self, sheet_names: list[str]):
        logger.info(f"Loading {len(sheet_names)} Sheet nodes...")
        rows = [
            {
                "name": name,
                "index": i,
                "description": SHEET_DESCRIPTIONS.get(name, ""),
            }
            for i, name in enumerate(sheet_names)
        ]
        self._run(
            """
            UNWIND $rows AS row
            MERGE (s:Sheet {name: row.name})
            SET s.index = row.index, s.description = row.description
            """,
            rows=rows,
        )

    def load_categories(self, categories: list[str]):
        logger.info(f"Loading {len(categories)} Category nodes...")
        rows = [{"name": c} for c in categories if c]
        self._run(
            """
            UNWIND $rows AS row
            MERGE (c:Category {name: row.name})
            """,
            rows=rows,
        )

    # ── Relationships ─────────────────────────────────────────────────────────

    def load_depends_on(self, edges: list[dict]):
        logger.info(f"Loading {len(edges)} DEPENDS_ON relationships...")
        for i in range(0, len(edges), _BATCH_SIZE):
            batch = edges[i : i + _BATCH_SIZE]
            self._run(
                """
                UNWIND $batch AS row
                MATCH (src:Indicator {id: row.source_id})
                MATCH (tgt:Indicator {id: row.target_id})
                MERGE (src)-[r:DEPENDS_ON {
                    source_id: row.source_id,
                    target_id: row.target_id
                }]->(tgt)
                SET r.operation        = row.operation,
                    r.formula_fragment = row.formula_fragment,
                    r.is_cross_sheet   = row.is_cross_sheet,
                    r.is_circular      = row.is_circular,
                    r.circular_group   = row.circular_group
                """,
                batch=batch,
            )
        logger.info("DEPENDS_ON relationships loaded.")

    def load_belongs_to(self, indicators: list[dict]):
        logger.info("Loading BELONGS_TO relationships...")
        rows = [{"id": ind["id"], "sheet": ind["sheet"]} for ind in indicators]
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            self._run(
                """
                UNWIND $batch AS row
                MATCH (n:Indicator {id: row.id})
                MATCH (s:Sheet {name: row.sheet})
                MERGE (n)-[:BELONGS_TO]->(s)
                """,
                batch=batch,
            )

    def load_in_category(self, indicators: list[dict]):
        logger.info("Loading IN_CATEGORY relationships...")
        rows = [
            {"id": ind["id"], "category": ind["category"]}
            for ind in indicators
            if ind.get("category")
        ]
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            self._run(
                """
                UNWIND $batch AS row
                MATCH (n:Indicator {id: row.id})
                MATCH (c:Category {name: row.category})
                MERGE (n)-[:IN_CATEGORY]->(c)
                """,
                batch=batch,
            )

    def load_feeds_into(self):
        logger.info("Loading FEEDS_INTO sheet relationships...")
        rows = [{"from": f, "to": t} for f, t in SHEET_FEED_INTO]
        self._run(
            """
            UNWIND $rows AS row
            MATCH (a:Sheet {name: row.from})
            MATCH (b:Sheet {name: row.to})
            MERGE (a)-[:FEEDS_INTO]->(b)
            """,
            rows=rows,
        )

    # ── Full load ─────────────────────────────────────────────────────────────

    def load_all(self, indicators: list[dict], edges: list[dict]):
        self.setup_schema()

        sheet_names = list(dict.fromkeys(ind["sheet"] for ind in indicators))
        categories = list(dict.fromkeys(
            ind["category"] for ind in indicators if ind.get("category")
        ))

        self.load_sheets(sheet_names)
        self.load_categories(categories)
        self.load_indicators(indicators)
        self.load_depends_on(edges)
        self.load_belongs_to(indicators)
        self.load_in_category(indicators)
        self.load_feeds_into()

        logger.info("All data loaded into Neo4j.")
