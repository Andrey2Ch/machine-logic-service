import urllib.request
import json

url = 'https://machine-logic-service-production.up.railway.app/lots/?status_filter=new,assigned,in_production&limit=200'

with urllib.request.urlopen(url, timeout=30) as response:
    lots = json.loads(response.read().decode())

targets = ['2530766', '2530767']

for lot_num in targets:
    lot = next((l for l in lots if l.get('lot_number') == lot_num), None)
    if lot:
        part = lot.get('part', {})
        print(f"\n=== Лот {lot_num} ===")
        print(f"  Status: {lot.get('status')}")
        print(f"  Machine: {lot.get('machine_name')} (id={lot.get('assigned_machine_id')})")
        print(f"  Order in queue: {lot.get('assigned_order')}")
        print(f"  Drawing: {part.get('drawing_number')}")
        print(f"  Diameter: {part.get('recommended_diameter')}мм")
        print(f"  Profile: {part.get('profile_type')}")
        print(f"  Quantity: {lot.get('initial_planned_quantity')}")
        print(f"  Cycle time: {part.get('avg_cycle_time')}сек")
        print(f"  Due date: {lot.get('due_date')}")
    else:
        print(f"\n=== Лот {lot_num} NOT FOUND ===")

