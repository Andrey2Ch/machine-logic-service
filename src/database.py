from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy import create_engine, Engine
from pydantic_settings import BaseSettings
from pydantic import Field
import os

class DatabaseSettings(BaseSettings):
    # Считываем URL из переменной окружения DATABASE_URL
    # Предоставляем значение по умолчанию для локальной разработки, если переменная не установлена
    url: str = Field(default=os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/isramat_bot"), alias='DATABASE_URL')

    class Config:
        # Если вы храните URL в .env файле для локальной разработки
        env_file = '.env'
        extra = 'ignore'

# Создаем экземпляр настроек
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
        engine = create_engine(db_settings.url)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        # Важно: НЕ вызываем create_all здесь. Миграции должны управляться отдельно.
        # Base.metadata.create_all(bind=engine) 
    print(f"Database engine initialized with URL: {db_settings.url[:15]}...") # Логируем часть URL для проверки

def get_db_session() -> Session:
    """FastAPI dependency to get a DB session."""
    if SessionLocal is None:
        # Это не должно происходить, если initialize_database вызван при старте
        raise RuntimeError("Database session factory not initialized.") 
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
