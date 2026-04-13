"""
validator.py

Verification queries for the Neo4j graph.
Run after loading to confirm graph integrity.
"""

import logging
from typing import Optional
from neo4j import GraphDatabase

logger = logging.getLogger(__name__)


class GraphValidator:
    def __init__(self, uri: str, user: str, password: str, task_id: Optional[str] = None):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.task_id = task_id

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _query(self, cypher: str, **params):
        with self.driver.session() as session:
            result = session.run(cypher, **params)
            return [dict(r) for r in result]

    def _task_filter(self, var: str = "n") -> str:
        """Return a WHERE/AND clause fragment for task_id filtering."""
        if self.task_id:
            return f"{var}.task_id = '{self.task_id}'"
        return "true"

    def run_all_checks(self) -> dict:
        results = {}
        tf = self._task_filter

        # 1. Node counts
        r = self._query(f"MATCH (n:Indicator) WHERE {tf()} RETURN count(n) AS cnt")
        results["indicator_count"] = r[0]["cnt"]

        r = self._query(f"MATCH (n:Sheet) WHERE {tf()} RETURN count(n) AS cnt")
        results["sheet_count"] = r[0]["cnt"]

        r = self._query(f"MATCH (n:Category) WHERE {tf()} RETURN count(n) AS cnt")
        results["category_count"] = r[0]["cnt"]

        # 2. Relationship counts
        r = self._query(f"MATCH (a:Indicator)-[r:DEPENDS_ON]->(b:Indicator) WHERE {tf('a')} RETURN count(r) AS cnt")
        results["depends_on_count"] = r[0]["cnt"]

        r = self._query(f"MATCH (a:Sheet)-[r:FEEDS_INTO]->(b:Sheet) WHERE {tf('a')} RETURN count(r) AS cnt")
        results["feeds_into_count"] = r[0]["cnt"]

        # 3. Orphan indicators
        r = self._query(
            f"""
            MATCH (n:Indicator)
            WHERE {tf()} AND n.is_input = false
              AND NOT (n)-[:DEPENDS_ON]-()
              AND NOT ()-[:DEPENDS_ON]->(n)
            RETURN count(n) AS cnt, collect(n.name)[..10] AS examples
            """
        )
        results["orphan_count"] = r[0]["cnt"]
        results["orphan_examples"] = r[0]["examples"]

        # 4. Circular paths exist
        r = self._query(
            f"""
            MATCH p=(n:Indicator)-[:DEPENDS_ON*2..6]->(n)
            WHERE {tf()}
            RETURN count(p) AS cnt
            LIMIT 1
            """
        )
        results["circular_paths_exist"] = r[0]["cnt"] > 0

        # 5. Key path: 营业收入 → 净利润
        r = self._query(
            f"""
            MATCH path = (a:Indicator)-[:DEPENDS_ON*1..5]->(b:Indicator)
            WHERE {tf('a')} AND a.name CONTAINS '营业收入' AND b.name CONTAINS '净利润'
            RETURN count(path) AS cnt
            LIMIT 1
            """
        )
        results["revenue_to_profit_path"] = r[0]["cnt"] > 0

        # 6. Balance sheet check
        r = self._query(
            f"MATCH (n:Indicator) WHERE {tf()} AND n.name CONTAINS '资产合计' RETURN count(n) AS cnt"
        )
        results["balance_sheet_node_exists"] = r[0]["cnt"] > 0

        # 7. IRR node exists
        r = self._query(
            f"MATCH (n:Indicator) WHERE {tf()} AND (n.name CONTAINS 'IRR' OR n.name CONTAINS '内部收益率') RETURN count(n) AS cnt"
        )
        results["irr_node_exists"] = r[0]["cnt"] > 0

        # 8. Cross-sheet edges
        r = self._query(
            f"MATCH (a:Indicator)-[r:DEPENDS_ON {{is_cross_sheet: true}}]->(b:Indicator) WHERE {tf('a')} RETURN count(r) AS cnt"
        )
        results["cross_sheet_edge_count"] = r[0]["cnt"]

        return results

    def print_report(self):
        results = self.run_all_checks()
        print("\n" + "=" * 50)
        print("Graph Validation Report")
        print("=" * 50)
        print(f"  Indicator nodes:      {results['indicator_count']:>6}  (expected 200-500)")
        print(f"  Sheet nodes:          {results['sheet_count']:>6}  (expected ~14)")
        print(f"  Category nodes:       {results['category_count']:>6}")
        print(f"  DEPENDS_ON edges:     {results['depends_on_count']:>6}  (expected 300-800)")
        print(f"  FEEDS_INTO edges:     {results['feeds_into_count']:>6}  (expected ~30)")
        print(f"  Cross-sheet edges:    {results['cross_sheet_edge_count']:>6}")
        print(f"  Orphan indicators:    {results['orphan_count']:>6}  (should be low)")
        if results["orphan_examples"]:
            print(f"    Examples: {results['orphan_examples']}")
        print(f"  Circular paths exist: {results['circular_paths_exist']}  (should be True)")
        print(f"  Revenue→Profit path:  {results['revenue_to_profit_path']}  (should be True)")
        print(f"  Balance sheet node:   {results['balance_sheet_node_exists']}  (should be True)")
        print(f"  IRR node exists:      {results['irr_node_exists']}  (should be True)")
        print("=" * 50)

        # Overall pass/fail
        checks = [
            results["indicator_count"] >= 100,
            results["sheet_count"] >= 10,
            results["depends_on_count"] >= 100,
            results["circular_paths_exist"],
            results["irr_node_exists"],
        ]
        passed = sum(checks)
        print(f"\nChecks passed: {passed}/{len(checks)}")
        if passed == len(checks):
            print("✓ Graph looks good!")
        else:
            print("✗ Some checks failed — review the parser output.")
        print()
