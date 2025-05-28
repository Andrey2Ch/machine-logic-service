#!/usr/bin/env python3
"""
Скрипт для создания таблицы карточек и тестирования API
"""

import psycopg2
import requests
import json

def create_cards_table():
    """Создание таблицы карточек и начальных данных"""
    try:
        # Подключение к базе данных
        conn = psycopg2.connect(
            host='localhost',
            database='isramat_bot',
            user='postgres',
            password='postgres'
        )
        
        print('✅ Подключение к базе данных успешно')
        
        # Читаем SQL скрипт
        with open('create_cards_table.sql', 'r', encoding='utf-8') as f:
            sql_script = f.read()
        
        # Выполняем скрипт
        cursor = conn.cursor()
        
        # Разбиваем на отдельные команды
        commands = sql_script.split(';')
        for cmd in commands:
            cmd = cmd.strip()
            if cmd and not cmd.startswith('--') and not cmd.startswith('SELECT'):
                print(f"Выполняю: {cmd[:50]}...")
                try:
                    cursor.execute(cmd)
                except Exception as cmd_error:
                    print(f"❌ Ошибка в команде: {cmd_error}")
                    print(f"Команда: {cmd}")
                    raise
        
        conn.commit()
        print('✅ SQL скрипт выполнен успешно')
        
        # Проверяем результат
        cursor.execute('SELECT COUNT(*) FROM cards')
        total_cards = cursor.fetchone()[0]
        print(f'📊 Создано карточек: {total_cards}')
        
        cursor.execute('SELECT COUNT(DISTINCT machine_id) FROM cards')
        machines_count = cursor.fetchone()[0]
        print(f'🏭 Станков с карточками: {machines_count}')
        
        # Показываем статистику по станкам
        cursor.execute("""
            SELECT 
                m.name as machine_name,
                COUNT(c.card_number) as cards_count
            FROM machines m
            LEFT JOIN cards c ON m.id = c.machine_id
            WHERE m.is_active = true
            GROUP BY m.id, m.name
            ORDER BY m.name
            LIMIT 5
        """)
        
        print('\n📋 Статистика по станкам (первые 5):')
        for row in cursor.fetchall():
            print(f"  {row[0]}: {row[1]} карточек")
        
        cursor.close()
        conn.close()
        return True
        
    except Exception as e:
        print(f'❌ Ошибка при создании таблицы: {e}')
        return False

def test_api_endpoints():
    """Тестирование API эндпоинтов карточек"""
    base_url = "http://localhost:8000"
    
    print('\n🧪 Тестирование API эндпоинтов...')
    
    try:
        # 1. Тест получения свободных карточек
        print('\n1. Тестируем GET /cards/free')
        response = requests.get(f"{base_url}/cards/free?machine_id=1")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Свободные карточки для станка 1: {data['cards'][:5]}...")
        else:
            print(f"❌ Ошибка: {response.status_code} - {response.text}")
        
        # 2. Тест гибкого поиска карточки
        print('\n2. Тестируем GET /cards/{card_id}/machine/{machine_code}')
        response = requests.get(f"{base_url}/cards/1/machine/1")
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Карточка найдена: {data['machine_name']}, статус: {data['status']}")
        else:
            print(f"❌ Ошибка: {response.status_code} - {response.text}")
        
        print('\n✅ Базовое тестирование API завершено')
        
    except requests.exceptions.ConnectionError:
        print('❌ Не удается подключиться к API. Убедитесь, что FastAPI сервер запущен на порту 8000')
    except Exception as e:
        print(f'❌ Ошибка при тестировании API: {e}')

if __name__ == "__main__":
    print("🚀 Настройка системы карточек для операторов")
    print("=" * 50)
    
    # Создаем таблицу и данные
    if create_cards_table():
        # Тестируем API
        test_api_endpoints()
        
        print("\n" + "=" * 50)
        print("✅ Система карточек готова к использованию!")
        print("\n📋 Следующие шаги:")
        print("1. Запустить FastAPI сервер: uvicorn src.main:app --reload --port 8000")
        print("2. Обновить Telegram-бот для работы с карточками")
        print("3. Создать Dashboard для управления карточками")
        print("4. Напечатать пластиковые карточки")
    else:
        print("\n❌ Настройка не завершена из-за ошибок") 