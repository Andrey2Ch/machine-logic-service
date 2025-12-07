import urllib.request
import json

url = 'https://machine-logic-service-production.up.railway.app/lots/?status_filter=assigned,in_production&limit=200'

with urllib.request.urlopen(url, timeout=30) as response:
    lots = json.loads(response.read().decode())

# Фильтруем SR-26 (machine_id=6)
sr26_lots = [l for l in lots if l.get('assigned_machine_id') == 6 or l.get('machine_name') == 'SR-26']

print(f"=== SR-26 (machine_id=6) - {len(sr26_lots)} лотов ===\n")

# Сортируем по assigned_order
sr26_lots.sort(key=lambda x: x.get('assigned_order') or 999)

for lot in sr26_lots:
    part = lot.get('part', {})
    print(f"Позиция {lot.get('assigned_order')}: {lot.get('lot_number')}")
    print(f"  Status: {lot.get('status')}")
    print(f"  Drawing: {part.get('drawing_number')}")
    print(f"  Diameter: {part.get('recommended_diameter')}мм")
    print(f"  Qty: {lot.get('initial_planned_quantity')}")
    print()

