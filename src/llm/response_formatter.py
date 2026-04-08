"""
response_formatter.py

Formats Neo4j query results for display in the CLI.
"""

import json


def format_results(results: list[dict], max_rows: int = 20) -> str:
    """Format query results as a readable table-like string."""
    if not results:
        return "(无结果)"

    # Truncate if too many rows
    truncated = len(results) > max_rows
    display = results[:max_rows]

    lines = []
    for i, row in enumerate(display, 1):
        parts = []
        for k, v in row.items():
            if v is None:
                continue
            if isinstance(v, float):
                parts.append(f"{k}: {v:,.2f}")
            elif isinstance(v, list):
                parts.append(f"{k}: [{', '.join(str(x) for x in v[:5])}{'...' if len(v) > 5 else ''}]")
            else:
                parts.append(f"{k}: {v}")
        lines.append(f"  {i}. " + " | ".join(parts))

    result_str = "\n".join(lines)
    if truncated:
        result_str += f"\n  ... (共{len(results)}条，仅显示前{max_rows}条)"
    return result_str
