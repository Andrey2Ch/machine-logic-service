import sqlite3

conn = sqlite3.connect('production.db')
cursor = conn.cursor()

# Показываем все таблицы
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
tables = cursor.fetchall()
print("Таблицы в базе данных:")
for table in tables:
    print(table[0])

# Показываем структуру каждой таблицы
for table in tables:
    table_name = table[0]
    print(f"\nСтруктура таблицы {table_name}:")
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    for col in columns:
        print(f"  {col[1]} ({col[2]})")

conn.close() 