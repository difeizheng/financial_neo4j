"""
analyzer.py — 试算结果分析器

生成技术视角和财务视角的数据：
- 技术视角：变化指标列表、影响路径图、数据完整性验证
- 财务视角：IRR/NPV变化、敏感度排名、趋势对比、热力图
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from src.graph.impact_analyzer import ImpactAnalyzer

logger = logging.getLogger(__name__)


# 关键财务指标关键词（用于识别核心指标）
KEY_FINANCIAL_KEYWORDS = [
    "IRR", "内部收益率", "净现值", "NPV", "回收期",
    "利润", "净利润", "营业收入", "现金流",
    "总投资", "资本金", "容量电价", "电量",
]


def _to_float(val) -> Optional[float]:
    """安全转换为 float，失败返回 None。"""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


class TrialAnalyzer:
    """分析试算结果，生成技术/财务视角数据。"""

    def __init__(self, trial_store, task_manager):
        self.trial_store = trial_store
        self.task_manager = task_manager

    # ── 技术视角 ───────────────────────────────────────────────────────────────

    def get_technical_view(self, trial_id: str, task_id: str) -> dict:
        """
        生成技术视角数据（增强版）。

        Returns:
            {
                "total_indicators": int,
                "source_changes": list[dict],       # 源头修改（用户主动修改的指标）
                "affected_indicators": list[dict],  # 被动影响（因依赖链变化的指标）
                "changed_indicators": list[dict],   # 所有变化指标（用于图形）
                "impact_by_depth": dict[int, list], # 按深度分组的影响指标
                "impact_edges": list[dict],         # 依赖边列表
                "source_ids": list[str],            # 源头指标 ID 列表
                "impact_stats": dict,               # 影响统计
                "validation": dict,
            }
        """
        trial = self.trial_store.get_trial(trial_id)
        if not trial:
            return {"error": "试算不存在"}

        # 1. 获取用户修改的指标（源头）
        change_logs = self.trial_store.get_change_logs(trial_id, include_deleted=False)
        source_ids = [log["indicator_id"] for log in change_logs]
        source_info = {log["indicator_id"]: log for log in change_logs}

        # 2. 构建影响链（使用 Neo4j 依赖图）
        impact_chain = self._build_impact_chain(source_ids, task_id)

        # 3. 获取重算结果
        results = self.trial_store.get_recalc_results(trial_id)

        # 4. 获取原始指标数据
        indicators_path = self.task_manager.get_indicators_path(task_id)
        if not indicators_path.exists():
            return {"error": "原始指标数据不存在"}

        original_indicators = json.loads(indicators_path.read_text(encoding="utf-8"))

        # 解析 values_json
        def _parse_values(ind):
            vals_raw = ind.get("values_json")
            if vals_raw:
                if isinstance(vals_raw, str):
                    try:
                        return json.loads(vals_raw)
                    except Exception:
                        return []
                elif isinstance(vals_raw, list):
                    return vals_raw
            return [ind.get("value_year1")]

        original_values = {
            ind["id"]: {
                "name": ind.get("name", ind["id"]),
                "values": _parse_values(ind),
                "sheet": ind.get("sheet", ""),
                "unit": ind.get("unit", ""),
                "formula_raw": ind.get("formula_raw", ""),
                "is_input": ind.get("is_input", False),
            }
            for ind in original_indicators
        }

        # 5. 找出变化的指标
        changed = []
        for ind_id, new_data in results.items():
            orig = original_values.get(ind_id)
            if not orig:
                continue

            new_vals = new_data.get("values", [])
            orig_vals = orig.get("values", [])

            if not new_vals or not orig_vals:
                continue

            # 比较第一年值
            new_y1 = _to_float(new_vals[0]) if new_vals else None
            orig_y1 = _to_float(orig_vals[0]) if orig_vals else None

            if new_y1 is None or orig_y1 is None:
                continue

            # 检查是否有变化
            if new_y1 != orig_y1:
                # 计算变化幅度
                if orig_y1 != 0:
                    pct_change = ((new_y1 - orig_y1) / abs(orig_y1)) * 100
                else:
                    pct_change = 100 if new_y1 else 0

                changed.append({
                    "id": ind_id,
                    "name": new_data.get("name", ind_id),
                    "original": orig_y1,
                    "new": new_y1,
                    "pct_change": pct_change,
                    "sheet": orig.get("sheet", ""),
                    "is_source": ind_id in source_ids,
                    "formula_raw": orig.get("formula_raw", ""),
                    "is_input": orig.get("is_input", False),
                    "unit": orig.get("unit", ""),
                })

        # 按变化幅度排序
        changed.sort(key=lambda x: abs(x.get("pct_change") or 0), reverse=True)

        # 6. 区分源头修改 vs 被动影响
        source_changes = [ind for ind in changed if ind["is_source"]]
        affected_indicators = [ind for ind in changed if not ind["is_source"]]

        # 7. 传递完整的 affected_by_depth（包含所有依赖节点）
        # 对于图形渲染，我们需要显示整个依赖网络
        affected_by_depth = impact_chain.get("affected_by_depth", {})
        impact_by_depth = {}
        changed_ids = set(ind["id"] for ind in affected_indicators)

        for depth, indicators in affected_by_depth.items():
            # 保留所有指标（无论值是否变化）
            depth_list = []
            for ind in indicators:
                ind_entry = {**ind, "original": None, "new": None, "pct_change": None}
                # 尝试匹配有变化的指标（ID格式可能不同，需要灵活匹配）
                matched = None
                for c in affected_indicators:
                    # 尝试多种匹配方式
                    if c["id"] == ind["id"]:
                        matched = c
                        break
                    # 简化ID匹配
                    c_simple = c["id"].split("__")[-1] if "__" in c["id"] else c["id"]
                    if c_simple == ind["id"]:
                        matched = c
                        break
                if matched:
                    ind_entry["original"] = matched["original"]
                    ind_entry["new"] = matched["new"]
                    ind_entry["pct_change"] = matched["pct_change"]
                depth_list.append(ind_entry)
            impact_by_depth[depth] = depth_list

        # 8. 影响统计
        impact_stats = {
            "total_changed": len(changed),
            "source_count": len(source_changes),
            "affected_count": len(affected_indicators),
            "max_depth": impact_chain.get("stats", {}).get("max_depth", 0),
            "max_change_pct": max(abs(c.get("pct_change") or 0) for c in changed) if changed else 0,
            "sheets_affected": len(set(c.get("sheet") for c in changed if c.get("sheet"))),
            "total_in_graph": sum(len(v) for v in affected_by_depth.values()) + len(source_ids),
        }

        # 9. 数据完整性验证
        validation = {
            "result_count": len(results),
            "indicator_count": len(original_indicators),
            "coverage_pct": len(results) / len(original_indicators) * 100 if original_indicators else 0,
            "has_year1_values": all(r.get("values") and len(r["values"]) > 0 for r in results.values()),
        }

        return {
            "total_indicators": len(results),
            "source_changes": source_changes,
            "affected_indicators": affected_indicators,
            "changed_indicators": changed[:50],  # 用于图形可视化
            "impact_by_depth": impact_by_depth,
            "impact_edges": impact_chain.get("edges", []),
            "source_ids": source_ids,  # 原始格式（用于其他逻辑）
            "source_ids_simplified": impact_chain.get("source_ids_simplified", source_ids),  # 简化格式（用于图形）
            "impact_stats": impact_stats,
            "validation": validation,
        }

    # ── 财务视角 ───────────────────────────────────────────────────────────────

    def get_financial_view(self, trial_id: str, task_id: str) -> dict:
        """
        生成财务视角数据。

        Returns:
            {
                "key_indicators": list[dict],  # IRR/NPV 等核心指标变化
                "sensitivity_ranking": list[dict],  # 敏感度排名
                "time_series": dict,  # 48 年趋势对比数据
                "heatmap": dict,  # 影响热力图数据
            }
        """
        trial = self.trial_store.get_trial(trial_id)
        if not trial:
            return {"error": "试算不存在"}

        results = self.trial_store.get_recalc_results(trial_id)

        # 加载原始指标
        indicators_path = self.task_manager.get_indicators_path(task_id)
        if not indicators_path.exists():
            return {"error": "原始指标数据不存在"}

        original_indicators = json.loads(indicators_path.read_text(encoding="utf-8"))

        # 解析 values_json（可能是字符串或列表）
        def _parse_vals(ind):
            vals_raw = ind.get("values_json")
            if vals_raw:
                if isinstance(vals_raw, str):
                    try:
                        return json.loads(vals_raw)
                    except Exception:
                        return []
                elif isinstance(vals_raw, list):
                    return vals_raw
            return [ind.get("value_year1")]

        original_values = {
            ind["id"]: {
                "name": ind.get("name", ind["id"]),
                "values": _parse_vals(ind),
                "unit": ind.get("unit", ""),
            }
            for ind in original_indicators
        }

        # 1. 关键财务指标变化
        key_indicators = []
        for kw in KEY_FINANCIAL_KEYWORDS:
            for ind_id, new_data in results.items():
                name = new_data.get("name", ind_id)
                if kw in name:
                    orig = original_values.get(ind_id)
                    if orig:
                        new_vals = new_data.get("values", [])
                        orig_vals = orig.get("values", [])
                        new_y1 = _to_float(new_vals[0]) if new_vals else None
                        orig_y1 = _to_float(orig_vals[0]) if orig_vals else None

                        # 跳过空值
                        if new_y1 is None or orig_y1 is None:
                            continue

                        if new_y1 != orig_y1:
                            pct_change = ((new_y1 - orig_y1) / abs(orig_y1) * 100) if orig_y1 != 0 else None
                            key_indicators.append({
                                "id": ind_id,
                                "name": name,
                                "original": orig_y1,
                                "new": new_y1,
                                "unit": orig.get("unit", ""),
                                "pct_change": pct_change,
                            })

        # 2. 敏感度排名（哪个指标变化导致关键指标变化最大）
        # 简化版：按变化幅度排序所有指标
        sensitivity_ranking = []
        for ind_id, new_data in results.items():
            orig = original_values.get(ind_id)
            if not orig:
                continue

            new_vals = new_data.get("values", [])
            orig_vals = orig.get("values", [])
            new_y1 = _to_float(new_vals[0]) if new_vals else None
            orig_y1 = _to_float(orig_vals[0]) if orig_vals else None

            # 跳过空值
            if new_y1 is None or orig_y1 is None:
                continue

            if new_y1 != orig_y1 and orig_y1 != 0:
                pct_change = ((new_y1 - orig_y1) / abs(orig_y1)) * 100
                sensitivity_ranking.append({
                    "id": ind_id,
                    "name": new_data.get("name", ind_id),
                    "pct_change": pct_change,
                })

        sensitivity_ranking.sort(key=lambda x: abs(x["pct_change"]), reverse=True)

        # 3. 时间序列趋势对比（取变化最大的指标）
        time_series = {}
        if sensitivity_ranking:
            top_ind_id = sensitivity_ranking[0]["id"]
            new_data = results.get(top_ind_id)
            orig = original_values.get(top_ind_id)
            if new_data and orig:
                time_series = {
                    "indicator_id": top_ind_id,
                    "indicator_name": new_data.get("name", top_ind_id),
                    "original": orig.get("values", []),
                    "new": new_data.get("values", []),
                }

        # 4. 影响热力图（按工作表分组）
        heatmap = self._build_heatmap(results, original_values, original_indicators)

        return {
            "key_indicators": key_indicators[:10],
            "sensitivity_ranking": sensitivity_ranking[:20],
            "time_series": time_series,
            "heatmap": heatmap,
        }

    # ── 辅助方法 ────────────────────────────────────────────────────────────────

    def _get_indicator_sheet(self, ind_id: str, indicators: list) -> str:
        for ind in indicators:
            if ind["id"] == ind_id:
                return ind.get("sheet", "")
        return ""

    def _build_impact_chain(self, source_ids: list[str], task_id: str) -> dict:
        """
        构建从源头指标出发的影响链。

        使用 ImpactAnalyzer 查询 Neo4j 依赖图，获取每个源头指标的下游影响。
        合并所有源头的影响，计算综合影响深度。

        Args:
            source_ids: 源头指标 ID 列表（用户主动修改的指标）
            task_id: 任务 ID

        Returns:
            {
                "source_ids": list[str],
                "affected_by_depth": {depth: [{id, name, sheet}]},
                "edges": [{source_id, target_id, source_name, target_name}],
                "stats": {"total_affected": int, "max_depth": int}
            }
        """
        if not source_ids:
            return {
                "source_ids": [],
                "affected_by_depth": {},
                "edges": [],
                "stats": {"total_affected": 0, "max_depth": 0},
            }

        try:
            with ImpactAnalyzer(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, task_id) as analyzer:
                all_downstream = {}
                all_edges = []

                # 对每个源头指标查询下游影响
                for source_id in source_ids:
                    downstream = analyzer.get_downstream(source_id, max_depth=8)
                    edges = analyzer.get_impact_edges(source_id, max_depth=8)

                    # 合并下游指标，取最小深度
                    for ind in downstream:
                        # Neo4j 返回的 ID 格式为 "task_id__indicator_id"，需要去掉前缀
                        ind_id = ind["id"]
                        if "__" in ind_id:
                            ind_id = ind_id.split("__")[-1]

                        if ind_id not in all_downstream:
                            all_downstream[ind_id] = {
                                "id": ind_id,
                                "name": ind.get("name", ind_id),
                                "sheet": ind.get("sheet", ""),
                                "depth": ind["depth"],
                            }
                        else:
                            # 取最小深度（最短路径）
                            all_downstream[ind_id]["depth"] = min(
                                all_downstream[ind_id]["depth"], ind["depth"]
                            )

                    all_edges.extend(edges)

                # 去重边并去掉 task_id 前缀
                unique_edges = []
                seen = set()
                for edge in all_edges:
                    src_id = edge["source_id"]
                    tgt_id = edge["target_id"]

                    key = (src_id, tgt_id)
                    if key not in seen:
                        seen.add(key)

                        # 去掉 task_id 前缀
                        if "__" in src_id:
                            src_id = src_id.split("__")[-1]
                        if "__" in tgt_id:
                            tgt_id = tgt_id.split("__")[-1]

                        unique_edges.append({
                            "source_id": src_id,
                            "target_id": tgt_id,
                            "source_name": edge.get("source_name", src_id),
                            "target_name": edge.get("target_name", tgt_id),
                        })

                # 按深度分组
                affected_by_depth = {}
                for ind_id, ind in all_downstream.items():
                    depth = ind["depth"]
                    if depth not in affected_by_depth:
                        affected_by_depth[depth] = []
                    affected_by_depth[depth].append({
                        "id": ind_id,
                        "name": ind["name"],
                        "sheet": ind.get("sheet", ""),
                    })

                # 计算统计
                max_depth = max(d["depth"] for d in all_downstream.values()) if all_downstream else 0

                # 简化 source_ids 格式（与 edges 中的ID格式保持一致）
                # source_ids 格式可能是 sheet__name__row，需要简化为 row 部分
                source_ids_simplified = []
                for sid in source_ids:
                    if "__" in sid:
                        # 取最后一部分（row 或 simple_id）
                        source_ids_simplified.append(sid.split("__")[-1])
                    else:
                        source_ids_simplified.append(sid)

                return {
                    "source_ids": source_ids,  # 保留原始格式
                    "source_ids_simplified": source_ids_simplified,  # 简化格式（用于图形）
                    "affected_by_depth": affected_by_depth,
                    "edges": unique_edges,
                    "stats": {
                        "total_affected": len(all_downstream),
                        "max_depth": max_depth,
                    },
                }

        except Exception as e:
            logger.exception(f"构建影响链失败: {e}")
            # 也简化错误情况下的 source_ids
            source_ids_simplified = []
            for sid in source_ids:
                if "__" in sid:
                    source_ids_simplified.append(sid.split("__")[-1])
                else:
                    source_ids_simplified.append(sid)
            return {
                "source_ids": source_ids,
                "source_ids_simplified": source_ids_simplified,
                "affected_by_depth": {},
                "edges": [],
                "stats": {"total_affected": 0, "max_depth": 0, "error": str(e)},
            }

    def _build_heatmap(
        self,
        results: dict,
        original_values: dict,
        indicators: list,
    ) -> dict:
        """构建按工作表分组的影响热力图数据。"""
        sheet_changes: dict[str, dict] = {}

        for ind in indicators:
            ind_id = ind["id"]
            sheet = ind.get("sheet", "未知")
            new_data = results.get(ind_id)
            orig = original_values.get(ind_id)

            if not new_data or not orig:
                continue

            new_vals = new_data.get("values", [])
            orig_vals = orig.get("values", [])
            new_y1 = _to_float(new_vals[0]) if new_vals else None
            orig_y1 = _to_float(orig_vals[0]) if orig_vals else None

            # 跳过空值或无变化
            if new_y1 is None or orig_y1 is None or new_y1 == orig_y1:
                continue

            # 计算变化幅度
            if orig_y1 != 0:
                pct_change = abs((new_y1 - orig_y1) / orig_y1) * 100
            else:
                pct_change = 100 if new_y1 else 0

            # 累计到工作表
            if sheet not in sheet_changes:
                sheet_changes[sheet] = {
                    "count": 0,
                    "total_change_pct": 0,
                    "max_change_pct": 0,
                    "indicators": [],
                }

            sheet_changes[sheet]["count"] += 1
            sheet_changes[sheet]["total_change_pct"] += pct_change
            sheet_changes[sheet]["max_change_pct"] = max(
                sheet_changes[sheet]["max_change_pct"], pct_change
            )
            sheet_changes[sheet]["indicators"].append({
                "name": ind.get("name", ind_id),
                "pct_change": pct_change,
            })

        # 计算平均变化幅度
        for sheet, data in sheet_changes.items():
            data["avg_change_pct"] = data["total_change_pct"] / data["count"] if data["count"] else 0

        return sheet_changes

    # ── 试算对比 ─────────────────────────────────────────────────────────────────

    def compare_trials(
        self,
        trial_id1: str,
        trial_id2: str,
        task_id: str,
    ) -> dict:
        """
        对比两个试算的关键指标差异。

        Returns:
            {
                "trial1": dict,
                "trial2": dict,
                "key_diffs": list[dict],
            }
        """
        trial1 = self.trial_store.get_trial(trial_id1)
        trial2 = self.trial_store.get_trial(trial_id2)

        if not trial1 or not trial2:
            return {"error": "试算不存在"}

        results1 = self.trial_store.get_recalc_results(trial_id1)
        results2 = self.trial_store.get_recalc_results(trial_id2)

        # 加载原始指标
        indicators_path = self.task_manager.get_indicators_path(task_id)
        if not indicators_path.exists():
            return {"error": "原始指标数据不存在"}

        original_indicators = json.loads(indicators_path.read_text(encoding="utf-8"))

        # 对比关键指标
        key_diffs = []
        for kw in KEY_FINANCIAL_KEYWORDS:
            for ind in original_indicators:
                name = ind.get("name", ind["id"])
                if kw in name:
                    ind_id = ind["id"]
                    r1 = results1.get(ind_id)
                    r2 = results2.get(ind_id)

                    if r1 and r2:
                        v1 = r1.get("values", [None])[0]
                        v2 = r2.get("values", [None])[0]

                        if v1 != v2:
                            diff = (v2 - v1) if v1 and v2 else None
                            key_diffs.append({
                                "id": ind_id,
                                "name": name,
                                "trial1_value": v1,
                                "trial2_value": v2,
                                "diff": diff,
                                "unit": ind.get("unit", ""),
                            })

        return {
            "trial1": {"name": trial1["name"], "status": trial1["status"]},
            "trial2": {"name": trial2["name"], "status": trial2["status"]},
            "key_diffs": key_diffs,
        }