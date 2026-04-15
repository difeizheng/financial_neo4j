"""
loader.py

Loads indicators and dependencies into Neo4j.
Uses UNWIND for batch operations to maximize performance.

Multi-task support: when task_id is provided, all nodes get a task_id property
and indicator IDs are prefixed with "{task_id}__" for global uniqueness.
When task_id is None (CLI path), behavior is identical to the original.
"""

import copy
import logging
from typing import Optional
from neo4j import GraphDatabase

from src.graph.schema import (
    CONSTRAINTS_AND_INDEXES,
    SHEET_FEED_INTO,
    SHEET_DESCRIPTIONS,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500


class GraphLoader:
    def __init__(self, uri: str, user: str, password: str, task_id: Optional[str] = None):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.task_id = task_id

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _run(self, query: str, **params):
        with self.driver.session() as session:
            return session.run(query, **params)

    def _prefix_id(self, indicator_id: str) -> str:
        """Prefix indicator ID with task_id for global uniqueness."""
        if self.task_id:
            return f"{self.task_id}__{indicator_id}"
        return indicator_id

    def _prepare_indicators(self, indicators: list[dict]) -> list[dict]:
        """Add task_id and prefix IDs when task_id is set."""
        if not self.task_id:
            return indicators
        result = []
        for ind in indicators:
            item = dict(ind)
            item["id"] = self._prefix_id(ind["id"])
            item["task_id"] = self.task_id
            result.append(item)
        return result

    def _prepare_edges(self, edges: list[dict]) -> list[dict]:
        """Prefix source/target IDs when task_id is set."""
        if not self.task_id:
            return edges
        result = []
        for edge in edges:
            item = dict(edge)
            item["source_id"] = self._prefix_id(edge["source_id"])
            item["target_id"] = self._prefix_id(edge["target_id"])
            result.append(item)
        return result

    # ── Schema setup ──────────────────────────────────────────────────────────

    def _drop_legacy_constraints(self):
        """Drop old single-property uniqueness constraints on Sheet.name and
        Category.name that were created by the original single-task CLI.
        These conflict with multi-task support where the same sheet name can
        appear in multiple tasks."""
        for label in ("Sheet", "Category"):
            try:
                with self.driver.session() as session:
                    result = session.run(
                        "SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties, type "
                        "WHERE labelsOrTypes = [$label] AND properties = ['name'] "
                        "AND type = 'UNIQUENESS' RETURN name",
                        label=label,
                    )
                    names = [r["name"] for r in result]
                for cname in names:
                    with self.driver.session() as session:
                        session.run(f"DROP CONSTRAINT `{cname}` IF EXISTS")
                    logger.info(f"Dropped legacy constraint: {cname}")
            except Exception as e:
                logger.warning(f"Could not inspect/drop legacy {label} constraint: {e}")

    def setup_schema(self):
        self._drop_legacy_constraints()
        logger.info("Creating constraints and indexes...")
        for stmt in CONSTRAINTS_AND_INDEXES:
            try:
                self._run(stmt)
            except Exception as e:
                logger.warning(f"Schema stmt skipped ({e}): {stmt[:60]}")

    # ── Clear task data ───────────────────────────────────────────────────────

    def clear_task_data(self):
        """Delete all nodes and relationships belonging to this task."""
        if not self.task_id:
            logger.warning("clear_task_data called without task_id — skipping")
            return
        logger.info(f"Clearing Neo4j data for task: {self.task_id}")
        self._run(
            "MATCH (n) WHERE n.task_id = $task_id DETACH DELETE n",
            task_id=self.task_id,
        )
        logger.info("Task data cleared.")

    # ── Nodes ─────────────────────────────────────────────────────────────────

    def load_indicators(self, indicators: list[dict]):
        prepared = self._prepare_indicators(indicators)
        logger.info(f"Loading {len(prepared)} Indicator nodes...")
        for i in range(0, len(prepared), _BATCH_SIZE):
            batch = prepared[i : i + _BATCH_SIZE]
            if self.task_id:
                self._run(
                    """
                    UNWIND $batch AS row
                    MERGE (n:Indicator {id: row.id})
                    SET n.task_id        = row.task_id,
                        n.name          = row.name,
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
                        n.values_json   = row.values_json,
                        n.parent_id     = row.parent_id,
                        n.parent_name   = row.parent_name
                    """,
                    batch=batch,
                )
            else:
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
                "task_id": self.task_id,
            }
            for i, name in enumerate(sheet_names)
        ]
        if self.task_id:
            self._run(
                """
                UNWIND $rows AS row
                MERGE (s:Sheet {name: row.name, task_id: row.task_id})
                SET s.index = row.index, s.description = row.description
                """,
                rows=rows,
            )
        else:
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
        rows = [{"name": c, "task_id": self.task_id} for c in categories if c]
        if self.task_id:
            self._run(
                """
                UNWIND $rows AS row
                MERGE (c:Category {name: row.name, task_id: row.task_id})
                """,
                rows=rows,
            )
        else:
            self._run(
                """
                UNWIND $rows AS row
                MERGE (c:Category {name: row.name})
                """,
                rows=rows,
            )

    # ── Relationships ─────────────────────────────────────────────────────────

    def load_depends_on(self, edges: list[dict]):
        prepared = self._prepare_edges(edges)
        logger.info(f"Loading {len(prepared)} DEPENDS_ON relationships...")
        for i in range(0, len(prepared), _BATCH_SIZE):
            batch = prepared[i : i + _BATCH_SIZE]
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

    def load_child_of(self, edges: list[dict]):
        """Load CHILD_OF parent-child relationship edges."""
        prepared = self._prepare_edges(edges)
        logger.info(f"Loading {len(prepared)} CHILD_OF relationships...")
        for i in range(0, len(prepared), _BATCH_SIZE):
            batch = prepared[i : i + _BATCH_SIZE]
            self._run(
                """
                UNWIND $batch AS row
                MATCH (child:Indicator {id: row.source_id})
                MATCH (parent:Indicator {id: row.target_id})
                MERGE (child)-[r:CHILD_OF]->(parent)
                """,
                batch=batch,
            )
        logger.info("CHILD_OF relationships loaded.")

    def load_belongs_to(self, indicators: list[dict]):
        logger.info("Loading BELONGS_TO relationships...")
        prepared = self._prepare_indicators(indicators)
        rows = [{"id": ind["id"], "sheet": ind["sheet"]} for ind in prepared]
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            if self.task_id:
                self._run(
                    """
                    UNWIND $batch AS row
                    MATCH (n:Indicator {id: row.id})
                    MATCH (s:Sheet {name: row.sheet, task_id: $task_id})
                    MERGE (n)-[:BELONGS_TO]->(s)
                    """,
                    batch=batch,
                    task_id=self.task_id,
                )
            else:
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
        prepared = self._prepare_indicators(indicators)
        rows = [
            {"id": ind["id"], "category": ind["category"]}
            for ind in prepared
            if ind.get("category")
        ]
        for i in range(0, len(rows), _BATCH_SIZE):
            batch = rows[i : i + _BATCH_SIZE]
            if self.task_id:
                self._run(
                    """
                    UNWIND $batch AS row
                    MATCH (n:Indicator {id: row.id})
                    MATCH (c:Category {name: row.category, task_id: $task_id})
                    MERGE (n)-[:IN_CATEGORY]->(c)
                    """,
                    batch=batch,
                    task_id=self.task_id,
                )
            else:
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
        if self.task_id:
            self._run(
                """
                UNWIND $rows AS row
                MATCH (a:Sheet {name: row.from, task_id: $task_id})
                MATCH (b:Sheet {name: row.to, task_id: $task_id})
                MERGE (a)-[:FEEDS_INTO]->(b)
                """,
                rows=rows,
                task_id=self.task_id,
            )
        else:
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

    def load_all(self, indicators: list[dict], edges: list[dict], child_edges: list[dict] = None):
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

        # Load CHILD_OF relationships
        if child_edges:
            self.load_child_of(child_edges)

        logger.info("All data loaded into Neo4j.")

    # ── Update values (for parameter modification) ─────────────────────────────

    def update_indicator_values(self, new_values: dict[str, list]):
        """Batch update value_year1 and values_json for multiple indicators."""
        rows = []
        for ind_id, vals in new_values.items():
            prefixed = self._prefix_id(ind_id)
            if isinstance(vals, list) and len(vals) > 0:
                # Neo4j cannot store lists containing None — replace with 0.0
                clean_vals = [v if v is not None else 0.0 for v in vals]
                row = {
                    "id": prefixed,
                    "value_year1": clean_vals[0],
                    "values_json": clean_vals if len(clean_vals) > 1 else None,
                }
                rows.append(row)
            elif isinstance(vals, (int, float)):
                row = {"id": prefixed, "value_year1": float(vals), "values_json": None}
                rows.append(row)

        if not rows:
            return

        # Process in batches of 500 to avoid large single transactions
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            self._run(
                """
                UNWIND $rows AS row
                MATCH (n:Indicator {id: row.id})
                SET n.value_year1 = row.value_year1,
                    n.values_json = row.values_json
                """,
                rows=batch,
            )
        logger.info(f"Updated values for {len(rows)} indicators.")
