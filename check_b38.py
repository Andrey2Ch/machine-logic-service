import urllib.request
import json

# Получаем данные канбан
url = 'https://isramat-dashboard-production.up.railway.app/api/lots/kanban'
with urllib.request.urlopen(url, timeout=30) as response:
    data = json.load(response)

print(f"Data type: {type(data)}")
if isinstance(data, dict):
    print(f"Keys: {data.keys()}")
elif isinstance(data, list):
    print(f"List length: {len(data)}")
    if data:
        print(f"First item keys: {data[0].keys() if isinstance(data[0], dict) else 'not dict'}")

# Ищем станок B-38 - данные это список лотов
all_lots = data if isinstance(data, list) else data.get('lots', [])

# Сначала найдем B-38 machine_id из лотов
b38_machine_id = None
for lot in all_lots:
    if lot.get('machine_name') and 'B-38' in lot.get('machine_name', ''):
        b38_machine_id = lot.get('assigned_machine_id')
        print(f"Found B-38 machine_id from lot: {b38_machine_id}")
        break

# Или получим станки отдельно
machines_url = 'https://isramat-dashboard-production.up.railway.app/api/machines/kanban'
try:
    with urllib.request.urlopen(machines_url, timeout=30) as response:
        machines_data = json.load(response)
    machines = machines_data if isinstance(machines_data, list) else machines_data.get('machines', [])
    b38 = [m for m in machines if 'B-38' in m.get('name', '')]
    if b38:
        print('=== Станок B-38 ===')
        print(f"  ID: {b38[0].get('id')}")
        print(f"  Name: {b38[0].get('name')}")
        b38_machine_id = b38[0].get('id')
except Exception as e:
    print(f"Error getting machines: {e}")

# Ищем лоты assigned на B-38
b38_lots = [l for l in all_lots if l.get('assigned_machine_id') == b38_machine_id]

print(f'\n=== Лоты назначенные на B-38 ({len(b38_lots)}) ===')
for lot in b38_lots[:10]:
    print(f"  ID: {lot.get('id')}, lot_number: {lot.get('lot_number')}, status: {lot.get('status')}")
    print(f"    assigned_order: {lot.get('assigned_order')}")
    print(f"    total_planned: {lot.get('total_planned_quantity')}, initial: {lot.get('initial_planned_quantity')}")
    print(f"    avg_cycle_time: {lot.get('avg_cycle_time')}")
    print(f"    drawing: {lot.get('drawing_number')}")
    print()

# Теперь проверяем запрос загрузки очереди
print('\n=== Анализ запроса queue_hours ===')
for lot in b38_lots:
    avg_cycle = lot.get('avg_cycle_time')
    total_qty = lot.get('total_planned_quantity')
    initial_qty = lot.get('initial_planned_quantity')
    status = lot.get('status')
    
    if status in ('assigned', 'in_production'):
        hours = 0
        if avg_cycle and total_qty:
            hours = (avg_cycle * total_qty) / 3600
        print(f"  Lot {lot.get('lot_number')} ({status}):")
        print(f"    avg_cycle_time={avg_cycle}, total_qty={total_qty}")
        print(f"    Calculated hours: {hours:.1f}")
        if not avg_cycle or not total_qty:
            print(f"    ⚠️ ПРОБЛЕМА: avg_cycle_time или total_qty = NULL, hours=0!")

