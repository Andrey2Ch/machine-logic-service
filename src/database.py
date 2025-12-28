from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import create_engine, Engine
from pydantic_settings import BaseSettings
import os
from typing import Generator

# ИЗМЕНЕННАЯ Pydantic модель для настроек
class DatabaseSettings(BaseSettings):
    # Явное объявление переменной, которую мы ожидаем из окружения.
    # Pydantic автоматически найдет переменную окружения с таким же именем (регистр не важен).
    # Значение по умолчанию используется ТОЛЬКО если переменная не найдена.
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/isramat_bot"

    class Config:
        # Указываем, что нужно также искать переменные в .env файле
        env_file = '.env'
        extra = 'ignore'

# Создаем единственный экземпляр настроек
db_settings = DatabaseSettings()

# Объявляем Base здесь
Base = declarative_base()

# Эти переменные будут инициализированы в main.py
engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None

def initialize_database():
    """Инициализирует engine и SessionLocal после загрузки конфигурации."""
    global engine, SessionLocal
    if engine is None:
        # Используем атрибут из нашего объекта настроек
        # Увеличиваем размер пула соединений для предотвращения timeout ошибок
        engine = create_engine(
            db_settings.DATABASE_URL,
            pool_size=5,  # Уменьшено для работы с несколькими workers
            max_overflow=10,  # 8 workers × (5+10) = 120 max connections
            pool_timeout=30,  # Таймаут ожидания соединения
            pool_recycle=3600,  # Переиспользование соединений через час
            pool_pre_ping=True,  # Проверка соединения перед использованием
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        # Важно: НЕ вызываем create_all здесь. Миграции должны управляться отдельно.
        # Base.metadata.create_all(bind=engine) 
    # Логируем часть URL для проверки, используем напрямую из объекта настроек
    # Увеличил длину выводимого URL для лучшей диагностики
    print(f"Current working directory: {os.getcwd()}")
    print(f".env file exists: {os.path.exists('.env')}")
    if os.path.exists('.env'):
        with open('.env', 'r') as f:
            print(f".env content: {f.read()}")
    print(f"DATABASE_URL from env: {os.getenv('DATABASE_URL')}")
    print(f"Database engine initialized with URL: {db_settings.DATABASE_URL}") 

def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency to get a DB session."""
    if SessionLocal is None:
        # Это не должно происходить, если initialize_database вызван при старте
        raise RuntimeError("Database session factory not initialized.") 
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
