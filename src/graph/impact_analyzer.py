"""
impact_analyzer.py — 影响分析模块

查询 Neo4j 的 DEPENDS_ON 图，分析修改某个指标后的下游影响链。
"""

from __future__ import annotations

import logging
from typing import Optional

from neo4j import GraphDatabase

logger = logging.getLogger(__name__)


class ImpactAnalyzer:
    """分析指标修改在依赖图中的下游影响范围。"""

    def __init__(self, uri: str, user: str, password: str, task_id: Optional[str] = None):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))
        self.task_id = task_id

    def close(self):
        self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _run(self, cypher: str, **params):
        with self.driver.session() as session:
            result = session.run(cypher, **params)
            return [dict(r) for r in result]

    def _prefix_id(self, indicator_id: str) -> str:
        """Prefix indicator ID with task_id for Neo4j matching."""
        if self.task_id:
            return f"{self.task_id}__{indicator_id}"
        return indicator_id

    def get_downstream(
        self, ind_id: str, max_depth: int = 8
    ) -> list[dict]:
        """
        查询 ind_id 的所有下游影响指标（直接+间接依赖者）。

        DEPENDS_ON 边方向：(A)-[:DEPENDS_ON]->(B) 表示 A 的公式引用了 B。
        所以要找到"修改 B 会影响哪些指标"，需要找所有指向 B 的路径。

        注意：ind_id 会自动添加 task_id 前缀以匹配 Neo4j 中存储的 ID。

        Returns:
            list of {id, name, sheet, depth}，按 depth 升序排列。
        """
        prefixed_id = self._prefix_id(ind_id)
        logger.info(f"[ImpactAnalyzer] get_downstream: ind_id={ind_id}, prefixed_id={prefixed_id}, task_id={self.task_id}, max_depth={max_depth}")

        # 注意：Cypher 不支持在 MATCH 模式中使用参数，所以 max_depth 必须嵌入字符串
        cypher = f"""
        MATCH path = (affected:Indicator)-[:DEPENDS_ON*1..{max_depth}]->(changed:Indicator)
        WHERE changed.id = $ind_id
          AND changed.task_id = $task_id
          AND affected.task_id = $task_id
        RETURN DISTINCT
            affected.id      AS id,
            affected.name    AS name,
            affected.sheet  AS sheet,
            affected.unit    AS unit,
            min(length(path)) AS depth
        ORDER BY depth, name
        """
        result = self._run(cypher, ind_id=prefixed_id, task_id=self.task_id)
        logger.info(f"[ImpactAnalyzer] get_downstream: found {len(result)} downstream indicators")
        return result

    def get_impact_edges(
        self, ind_id: str, max_depth: int = 8
    ) -> list[dict]:
        """
        查询 ind_id 影响子图中的所有 DEPENDS_ON 边。
        """
        prefixed_id = self._prefix_id(ind_id)

        # 注意：Cypher 不支持在 MATCH 模式中使用参数，所以 max_depth 必须嵌入字符串
        cypher = f"""
        MATCH path = (affected:Indicator)-[:DEPENDS_ON*1..{max_depth}]->(changed:Indicator)
        WHERE changed.id = $ind_id
          AND changed.task_id = $task_id
          AND affected.task_id = $task_id
        WITH collect(DISTINCT affected.id) + collect(DISTINCT changed.id) AS subgraph_nodes

        UNWIND subgraph_nodes AS node_id
        MATCH (src:Indicator)-[:DEPENDS_ON]->(tgt:Indicator)
        WHERE src.task_id = $task_id
          AND tgt.task_id = $task_id
          AND src.id IN subgraph_nodes
          AND tgt.id IN subgraph_nodes
        RETURN DISTINCT
            src.id   AS source_id,
            src.name AS source_name,
            tgt.id   AS target_id,
            tgt.name AS target_name
        """
        return self._run(cypher, ind_id=prefixed_id, task_id=self.task_id)

    def get_impact_stats(self, ind_id: str, max_depth: int = 8) -> dict:
        """返回影响统计摘要。"""
        downstream = self.get_downstream(ind_id, max_depth)
        if not downstream:
            return {"count": 0, "max_depth": 0, "sheets": []}

        max_depth_val = max(d["depth"] for d in downstream)
        sheets = sorted(set(d.get("sheet", "") for d in downstream if d.get("sheet")))

        return {
            "count": len(downstream),
            "max_depth": max_depth_val,
            "sheets": sheets,
        }
