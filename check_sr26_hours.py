import urllib.request
import json

url = 'https://machine-logic-service-production.up.railway.app/lots/?status_filter=assigned,in_production&limit=200'

with urllib.request.urlopen(url, timeout=30) as response:
    lots = json.loads(response.read().decode())

sr26_lots = [l for l in lots if l.get('assigned_machine_id') == 6]
sr26_lots.sort(key=lambda x: x.get('assigned_order') or 999)

print(f"=== SR-26 queue calculation ===\n")
total_hours = 0

for lot in sr26_lots:
    part = lot.get('part', {})
    qty = lot.get('total_planned_quantity') or lot.get('initial_planned_quantity') or 0
    cycle = part.get('avg_cycle_time')
    produced = lot.get('actual_produced') or 0
    remaining = max(0, qty - produced)
    
    hours = 0
    if cycle and remaining > 0:
        hours = (cycle * remaining) / 3600
    
    total_hours += hours
    
    print(f"{lot.get('lot_number')} ({part.get('drawing_number')})")
    print(f"  qty={qty}, produced={produced}, remaining={remaining}")
    print(f"  cycle_time={cycle}сек")
    print(f"  hours={hours:.1f}ч")
    print()

print(f"TOTAL: {total_hours:.1f}ч = {total_hours/24:.1f} дней")

