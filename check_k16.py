import urllib.request
import json

url = 'https://machine-logic-service-production.up.railway.app/lots/?status_filter=assigned,in_production&limit=200'

with urllib.request.urlopen(url, timeout=30) as response:
    lots = json.loads(response.read().decode())

print(f"Total lots: {len(lots)}")

# Группируем по machine_name
machines = {}
for lot in lots:
    mn = lot.get('machine_name') or 'Unknown'
    if mn not in machines:
        machines[mn] = []
    machines[mn].append(lot)

# Показываем K-16 станки
for mn, mlots in sorted(machines.items()):
    if 'K-16' in mn or 'K16' in mn:
        total_hours = 0
        print(f"\n=== {mn} ({len(mlots)} lots) ===")
        for l in mlots:
            qty = l.get('total_planned_quantity') or l.get('initial_planned_quantity') or 0
            cycle = l.get('part', {}).get('avg_cycle_time')
            produced = l.get('actual_produced') or 0
            status = l.get('status')
            
            if cycle and qty > 0:
                if status == 'in_production' and produced:
                    remaining = max(0, qty - produced)
                    hours = (cycle * remaining) / 3600
                else:
                    hours = (cycle * qty) / 3600
                total_hours += hours
                print(f"  {l['lot_number']}: status={status}, qty={qty}, produced={produced}, cycle={cycle}s, hours={hours:.1f}h")
            else:
                print(f"  {l['lot_number']}: status={status}, qty={qty}, cycle={cycle} - NO CALC")
        
        print(f"  TOTAL: {total_hours:.1f} hours = {total_hours/24:.1f} days")

