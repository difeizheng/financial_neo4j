"""Test recalculation flow"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config
from src.task.manager import TaskManager
from src.graph.recalculator import ParameterRecalculator

tm = TaskManager(config.TASKS_DIR)
task_id = '5697dbb6062f4af383a173dae2d598f8'

# Load data
excel_path = tm.get_excel_path(task_id)
indicators = json.loads(tm.get_indicators_path(task_id).read_text(encoding='utf-8'))
task_config = json.loads(tm.get_config_path(task_id).read_text(encoding='utf-8'))
sheet_configs = task_config.get('sheet_configs', {})

print(f'Excel exists: {excel_path.exists()}')
print(f'Indicators: {len(indicators)}')

# Find editable indicator
editable = [i for i in indicators if i.get('is_input') and not str(i.get('formula_raw') or '').startswith('=')]
test_ind = editable[0]
print(f'Test: {test_ind["name"][:30]} (unit={test_ind.get("unit", "N/A")})')

# Create recalculator
recalc = ParameterRecalculator(
    task_id=task_id,
    excel_path=str(excel_path),
    indicators=indicators,
    sheet_configs=sheet_configs,
)

print('Recalculator created, starting recalc...')

# Run recalculation with small change
changes = {test_ind['id']: 1000.0}
result = recalc.recalculate(changes=changes)

print(f'Recalc completed: {len(result)} indicators')
print(f'Sample result: {test_ind["name"][:20]} = {result.get(test_ind["id"], ["N/A"])[0]}')