# -*- coding: utf-8 -*-
import json
from pathlib import Path

tasks_dir = Path('tasks')
task_dirs = sorted(tasks_dir.iterdir(), key=lambda d: d.stat().st_mtime, reverse=True)
task_id = task_dirs[0].name
print(f'Task ID: {task_id}')

ind_path = Path('tasks') / task_id / 'indicators.json'
data = json.loads(ind_path.read_text(encoding='utf-8'))

# 找 row=33 的所有指标，筛选 sheet 为 参数输入表 的
matches = [i for i in data if i.get('row') == 33 and i.get('sheet') == '参数输入表']
for m in matches:
    print(f'=== 实际装机容量 ===')
    print(f'id: {m["id"]}')
    print(f'name: {m["name"]}')
    print(f'sheet: {m.get("sheet")}')
    print(f'is_input: {m.get("is_input")}')
    print()

# 查看 dependencies.json 中，哪些边指向这个指标
dep_path = Path('tasks') / task_id / 'dependencies.json'
deps = json.loads(dep_path.read_text(encoding='utf-8'))

# 找 target_id 包含 "实际装机容量" 的
target_keyword = "实际装机容量"
matching_deps = [d for d in deps if target_keyword in d.get('target_id', '')]
print(f'\n=== 指向「实际装机容量」的依赖边 ===')
print(f'数量: {len(matching_deps)}')
for d in matching_deps[:10]:
    print(f'  {d["source_id"]} -> {d["target_id"]}')
    print(f'    operation: {d.get("operation")}, formula_fragment: {d.get("formula_fragment")}')

# 找 source_id 包含 "实际装机容量" 的（这个指标依赖了谁）
source_matching = [d for d in deps if target_keyword in d.get('source_id', '')]
print(f'\n=== 「实际装机容量」依赖的指标 ===')
print(f'数量: {len(source_matching)}')
for d in source_matching[:10]:
    print(f'  {d["source_id"]} -> {d["target_id"]}')