# -*- coding: utf-8 -*-
import json
from pathlib import Path

tasks_dir = Path('tasks')
# 按修改时间排序
task_dirs = sorted(tasks_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
task_id = task_dirs[0].name
print(f'Task ID: {task_id}')

ind_path = Path('tasks') / task_id / 'indicators.json'
data = json.loads(ind_path.read_text(encoding='utf-8'))

# 用 row=33 匹配（实际装机容量在第33行）
matches = [i for i in data if i.get('row') == 33]
for m in matches:
    print(f'id: {m["id"]}')
    print(f'name: {m["name"]}')
    print(f'row: {m.get("row")}')
    print(f'sheet: {m.get("sheet")}')
    print(f'is_input: {m.get("is_input")}')
    print()

# 找 DEPENDS_ON 边，看看哪些指标依赖这个指标
dep_path = Path('tasks') / task_id / 'dependencies.json'
if dep_path.exists():
    deps = json.loads(dep_path.read_text(encoding='utf-8'))
    # 找 target_id 包含 row 33 的
    # 先从 id 格式推断
    target_ids = [d['target_id'] for d in deps]
    print('Total dependencies:', len(target_ids))
    # 打印几个样例
    for tid in target_ids[:5]:
        print(f'  {tid}')