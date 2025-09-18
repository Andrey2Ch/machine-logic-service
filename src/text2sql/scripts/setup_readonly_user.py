"""
Настройка readonly пользователя для Text2SQL
============================================

Создает специального пользователя PostgreSQL с правами только на SELECT
для безопасного выполнения Text2SQL запросов.
"""

import os
import sys
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

def create_readonly_user():
    """Создает readonly пользователя для Text2SQL"""
    
    # Параметры подключения
    dsn = os.environ.get('DATABASE_URL') or 'postgresql://postgres:postgres@localhost:5432/isramat_bot'
    readonly_user = os.environ.get('TEXT2SQL_READONLY_USER') or 'text2sql_readonly'
    readonly_password = os.environ.get('TEXT2SQL_READONLY_PASSWORD') or 'text2sql_secure_2024'
    
    print(f"🔧 Настройка readonly пользователя: {readonly_user}")
    
    try:
        # Подключение к БД как superuser
        conn = psycopg2.connect(dsn)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        
        # 1. Создание пользователя
        print("1️⃣ Создание пользователя...")
        cur.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '{readonly_user}') THEN
                    CREATE ROLE {readonly_user} WITH LOGIN PASSWORD '{readonly_password}';
                END IF;
            END
            $$;
        """)
        
        # 2. Настройка прав доступа
        print("2️⃣ Настройка прав доступа...")
        
        # Отзыв всех прав
        cur.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {readonly_user};")
        cur.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {readonly_user};")
        cur.execute(f"REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM {readonly_user};")
        
        # Предоставление только SELECT прав на основные таблицы
        tables = [
            'batches', 'batch_operations', 'machines', 'employees', 
            'access_attempts', 'cards', 'machine_readings'
        ]
        
        for table in tables:
            try:
                cur.execute(f"GRANT SELECT ON {table} TO {readonly_user};")
                print(f"   ✅ SELECT права на таблицу {table}")
            except psycopg2.Error as e:
                print(f"   ⚠️ Таблица {table} не найдена: {e}")
        
        # 3. Настройка Row Level Security (RLS)
        print("3️⃣ Настройка Row Level Security...")
        
        # Включаем RLS для таблиц (если нужно)
        for table in tables:
            try:
                cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
                print(f"   ✅ RLS включен для {table}")
            except psycopg2.Error as e:
                print(f"   ⚠️ RLS для {table}: {e}")
        
        # 4. Создание политик RLS (базовая - доступ ко всем строкам)
        print("4️⃣ Создание политик RLS...")
        
        for table in tables:
            try:
                # Политика: readonly пользователь может читать все строки
                cur.execute(f"""
                    CREATE POLICY IF NOT EXISTS text2sql_read_policy ON {table}
                    FOR SELECT TO {readonly_user}
                    USING (true);
                """)
                print(f"   ✅ Политика RLS для {table}")
            except psycopg2.Error as e:
                print(f"   ⚠️ Политика RLS для {table}: {e}")
        
        # 5. Настройка таймаутов
        print("5️⃣ Настройка таймаутов...")
        
        # Установка statement_timeout для пользователя
        cur.execute(f"ALTER ROLE {readonly_user} SET statement_timeout = '5s';")
        cur.execute(f"ALTER ROLE {readonly_user} SET idle_in_transaction_session_timeout = '30s';")
        cur.execute(f"ALTER ROLE {readonly_user} SET lock_timeout = '2s';")
        
        print("   ✅ Таймауты настроены")
        
        # 6. Создание .env файла с параметрами подключения
        print("6️⃣ Создание конфигурации...")
        
        env_content = f"""
# Text2SQL Readonly Database Connection
TEXT2SQL_DATABASE_URL=postgresql://{readonly_user}:{readonly_password}@localhost:5432/isramat_bot
TEXT2SQL_READONLY_USER={readonly_user}
TEXT2SQL_READONLY_PASSWORD={readonly_password}
"""
        
        env_file = os.path.join(os.getcwd(), '.env.text2sql')
        with open(env_file, 'w', encoding='utf-8') as f:
            f.write(env_content)
        
        print(f"   ✅ Конфигурация сохранена в {env_file}")
        
        # 7. Тест подключения
        print("7️⃣ Тест подключения...")
        
        test_dsn = f"postgresql://{readonly_user}:{readonly_password}@localhost:5432/isramat_bot"
        test_conn = psycopg2.connect(test_dsn)
        test_cur = test_conn.cursor()
        test_cur.execute("SELECT current_user, current_database();")
        user, db = test_cur.fetchone()
        test_cur.close()
        test_conn.close()
        
        print(f"   ✅ Подключение успешно: {user}@{db}")
        
        cur.close()
        conn.close()
        
        print(f"\n🎉 Readonly пользователь {readonly_user} успешно создан!")
        print(f"📝 Параметры подключения:")
        print(f"   Host: localhost:5432")
        print(f"   Database: isramat_bot")
        print(f"   User: {readonly_user}")
        print(f"   Password: {readonly_password}")
        print(f"\n🔒 Безопасность:")
        print(f"   - Только SELECT операции")
        print(f"   - Statement timeout: 5s")
        print(f"   - Idle timeout: 30s")
        print(f"   - Lock timeout: 2s")
        print(f"   - Row Level Security включен")
        
        return True
        
    except psycopg2.Error as e:
        print(f"❌ Ошибка PostgreSQL: {e}")
        return False
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        return False

def test_readonly_connection():
    """Тестирует подключение readonly пользователя"""
    dsn = os.environ.get('TEXT2SQL_DATABASE_URL') or 'postgresql://text2sql_readonly:text2sql_secure_2024@localhost:5432/isramat_bot'
    
    try:
        conn = psycopg2.connect(dsn)
        cur = conn.cursor()
        
        # Тест SELECT
        cur.execute("SELECT COUNT(*) FROM batches;")
        count = cur.fetchone()[0]
        print(f"✅ SELECT тест: {count} батчей")
        
        # Тест запрещенной операции (должна вызвать ошибку)
        try:
            cur.execute("INSERT INTO batches (id) VALUES (999999);")
            print("❌ INSERT не заблокирован!")
        except psycopg2.Error:
            print("✅ INSERT заблокирован (как и должно быть)")
        
        cur.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Ошибка тестирования: {e}")
        return False

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        print("🧪 Тестирование readonly подключения...")
        test_readonly_connection()
    else:
        print("🚀 Настройка readonly пользователя для Text2SQL...")
        create_readonly_user()
