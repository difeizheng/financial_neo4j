# -*- coding: utf-8 -*-
"""诊断脚本：验证 Neo4j 中的实际 ID 格式和 task_id"""
import json
from pathlib import Path
from neo4j import GraphDatabase
import config

# 1. 找到最新任务
tasks_dir = Path('tasks')
task_dirs = sorted(tasks_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
task_id = task_dirs[0].name
print(f'JSON 中的 task_id: {task_id}')

# 2. 从 JSON 中找到 实际装机容量 的原始 ID
ind_path = Path('tasks') / task_id / 'indicators.json'
data = json.loads(ind_path.read_text(encoding='utf-8'))

# 找 row=33 且 sheet 包含 参数输入
target = None
for i in data:
    row = i.get('row')
    sheet = i.get('sheet', '')
    name = i.get('name', '')
    if row == 33 and '参数输入' in sheet:
        target = i
        break

if target:
    raw_id = target['id']
    print(f'JSON 中的原始 ID: {raw_id}')
    expected_prefixed = f'{task_id}__{raw_id}'
    print(f'预期的 Neo4j ID (加前缀): {expected_prefixed}')

# 3. 直接查询 Neo4j，看看实际存储的是什么
driver = GraphDatabase.driver(config.NEO4J_URI, auth=(config.NEO4J_USER, config.NEO4J_PASSWORD))

with driver.session() as session:
    # 查询所有包含 "实际装机容量" 的节点
    result = session.run("""
        MATCH (n:Indicator)
        WHERE n.name CONTAINS '装机' OR n.name CONTAINS '实际'
        RETURN n.id AS id, n.name AS name, n.task_id AS task_id
        LIMIT 10
    """)
    rows = [dict(r) for r in result]
    print(f'\n=== Neo4j 中包含"装机"或"实际"的节点 ===')
    print(f'数量: {len(rows)}')
    for row in rows:
        print(f'  id={row["id"]}')
        print(f'  name={row["name"]}')
        print(f'  task_id={row["task_id"]}')
        print()

    # 精确查询 raw_id 和 prefixed_id
    print(f'=== 精确查询 ID ===')
    result2 = session.run("""
        MATCH (n:Indicator)
        WHERE n.id = $raw_id OR n.id = $prefixed
        RETURN n.id AS id, n.name AS name, n.task_id AS task_id
    """, raw_id=raw_id, prefixed=expected_prefixed)
    rows2 = [dict(r) for r in result2]
    print(f'精确匹配数量: {len(rows2)}')
    for row in rows2:
        print(f'  id={row["id"]}')
        print(f'  name={row["name"]}')
        print()

    # 查询有多少节点以 task_id 开头
    result3 = session.run("""
        MATCH (n:Indicator)
        WHERE n.id STARTS WITH $tid
        RETURN count(n) AS cnt
        LIMIT 1
    """, tid=task_id)
    rows3 = [dict(r) for r in result3]
    print(f'以 task_id "{task_id[:8]}..." 开头的节点数: {rows3[0]["cnt"] if rows3 else "N/A"}')

    # 查询有多少 DEPENDS_ON 边指向 实际装机容量
    result4 = session.run("""
        MATCH (affected:Indicator)-[r:DEPENDS_ON]->(changed:Indicator)
        WHERE changed.id CONTAINS '实际装机' AND changed.task_id = $tid
        RETURN count(r) AS cnt
        LIMIT 1
    """, tid=task_id)
    rows4 = [dict(r) for r in result4]
    print(f'指向 实际装机容量 的 DEPENDS_ON 边数: {rows4[0]["cnt"] if rows4 else "N/A"}')

    # 不带 task_id 过滤的查询
    result5 = session.run("""
        MATCH (affected:Indicator)-[r:DEPENDS_ON]->(changed:Indicator)
        WHERE changed.id CONTAINS '实际装机'
        RETURN count(r) AS cnt
        LIMIT 1
    """)
    rows5 = [dict(r) for r in result5]
    print(f'指向 实际装机容量 的 DEPENDS_ON 边数(不限task_id): {rows5[0]["cnt"] if rows5 else "N/A"}')

    # 检查 node 的 task_id 是什么
    result6 = session.run("""
        MATCH (n:Indicator)
        WHERE n.id CONTAINS '实际装机'
        RETURN n.id AS id, n.task_id AS task_id
        LIMIT 5
    """)
    rows6 = [dict(r) for r in result6]
    print(f'\n=== 实际装机容量 节点的 task_id ===')
    for row in rows6:
        print(f'  id={row["id"]}')
        print(f'  task_id={row["task_id"]}')

driver.close()
