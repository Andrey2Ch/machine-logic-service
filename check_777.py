import sqlite3

conn = sqlite3.connect('production.db')
cursor = conn.cursor()

# Проверяем лоты с чертежом 777-77
cursor.execute("SELECT * FROM lots WHERE drawing_number LIKE '%777-77%'")
lots = cursor.fetchall()
print("Лоты с чертежом 777-77:")
for lot in lots:
    print(lot)

# Проверяем наладки для этих лотов
if lots:
    lot_ids = [lot[0] for lot in lots]
    placeholders = ','.join(['?' for _ in lot_ids])
    cursor.execute(f"SELECT * FROM setups WHERE lot_id IN ({placeholders})", lot_ids)
    setups = cursor.fetchall()
    print("\nНаладки для этих лотов:")
    for setup in setups:
        print(setup)

conn.close() 