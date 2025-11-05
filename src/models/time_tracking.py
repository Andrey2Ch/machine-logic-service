"""
SQLAlchemy модели для системы учета рабочего времени
"""
from sqlalchemy import Column, Integer, String, DateTime, Boolean, Numeric, Date, ForeignKey, Text, LargeBinary
from sqlalchemy.orm import relationship
from src.database import Base
from datetime import datetime


class TimeEntryDB(Base):
    """Записи входа/выхода сотрудников"""
    __tablename__ = 'time_entries'
    
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey('employees.id'), nullable=False)
    entry_type = Column(String(10), nullable=False)  # check_in или check_out
    entry_time = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    
    # Метод фиксации
    method = Column(String(20), nullable=False)  # telegram, terminal, web, manual
    
    # Геолокация (для Telegram)
    latitude = Column(Numeric(10, 8))
    longitude = Column(Numeric(11, 8))
    location_accuracy = Column(Numeric(10, 2))
    is_location_valid = Column(Boolean, default=True)
    
    # Данные терминала
    terminal_device_id = Column(String(255))
    face_confidence = Column(Numeric(5, 2))  # уверенность распознавания 0-100
    
    # Offline синхронизация
    client_timestamp = Column(DateTime(timezone=True))  # время на устройстве
    synced_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    
    # Корректировки
    is_manual_correction = Column(Boolean, default=False)
    corrected_by = Column(Integer, ForeignKey('employees.id'))
    correction_reason = Column(Text)
    
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    employee = relationship("EmployeeDB", foreign_keys=[employee_id])
    corrector = relationship("EmployeeDB", foreign_keys=[corrected_by])


class TerminalDB(Base):
    """Зарегистрированные терминалы"""
    __tablename__ = 'terminals'
    
    id = Column(Integer, primary_key=True)
    device_id = Column(String(255), unique=True, nullable=False)
    device_name = Column(String(255), nullable=False)
    location_description = Column(Text)
    is_active = Column(Boolean, default=True)
    last_seen_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class FaceEmbeddingDB(Base):
    """Векторы лиц для распознавания"""
    __tablename__ = 'face_embeddings'
    
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey('employees.id'), nullable=False)
    embedding = Column(LargeBinary, nullable=False)  # сериализованный вектор лица
    photo_url = Column(Text)  # ссылка на фото
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    
    # Relationships
    employee = relationship("EmployeeDB")


class WorkShiftDB(Base):
    """Агрегированные данные по сменам"""
    __tablename__ = 'work_shifts'
    
    id = Column(Integer, primary_key=True)
    employee_id = Column(Integer, ForeignKey('employees.id'), nullable=False)
    shift_date = Column(Date, nullable=False)
    check_in_time = Column(DateTime(timezone=True))
    check_out_time = Column(DateTime(timezone=True))
    total_hours = Column(Numeric(5, 2))
    status = Column(String(20))  # complete, incomplete, absent, corrected
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    employee = relationship("EmployeeDB")

