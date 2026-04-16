# -*- coding: utf-8 -*-
"""测试 ImpactAnalyzer.get_downstream() 方法"""
import json
from pathlib import Path
from neo4j import GraphDatabase
import config

# 1. 找到最新任务
tasks_dir = Path('tasks')
task_dirs = sorted(tasks_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
task_id = task_dirs[0].name

# 2. 从 JSON 中找到 实际装机容量 的原始 ID
ind_path = Path('tasks') / task_id / 'indicators.json'
data = json.loads(ind_path.read_text(encoding='utf-8'))

target = None
for i in data:
    row = i.get('row')
    sheet = i.get('sheet', '')
    if row == 33 and '参数输入' in sheet:
        target = i
        break

if target:
    raw_id = target['id']
    print(f'原始 ID (无前缀): {raw_id}')
    prefixed_id = f'{task_id}__{raw_id}'
    print(f'前缀 ID: {prefixed_id}')
    print(f'task_id: {task_id}')
    print()

# 3. 直接用 Cypher 测试 get_downstream 的查询
driver = GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))

with driver.session() as session:
    # 模拟 get_downstream 的查询
    result = session.run("""
        MATCH path = (affected:Indicator)-[:DEPENDS_ON*1..8]->(changed:Indicator)
        WHERE changed.id = $ind_id
          AND changed.task_id = $task_id
          AND affected.task_id = $task_id
        RETURN DISTINCT
            affected.id      AS id,
            affected.name    AS name,
            affected.sheet  AS sheet,
            min(length(path)) AS depth
        ORDER BY depth, name
    """, ind_id=prefixed_id, max_depth=8, task_id=task_id)

    rows = [dict(r) for r in result]
    print(f'=== get_downstream 结果 ===')
    print(f'找到 {len(rows)} 个下游指标')

    # 分组统计
    depth_groups = {}
    for row in rows:
        d = row.get('depth', 0)
        depth_groups.setdefault(d, []).append(row)

    for depth in sorted(depth_groups.keys()):
        items = depth_groups[depth]
        print(f'\n深度 {depth}: {len(items)} 个')
        for item in items[:3]:
            print(f'  - {item["name"]} ({item["sheet"]})')
        if len(items) > 3:
            print(f'  ... 还有 {len(items)-3} 个')

driver.close()