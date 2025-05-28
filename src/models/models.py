from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Boolean, Float, Text, BigInteger, CheckConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from .setup import SetupStatus
from ..database import Base
from sqlalchemy.sql import func

class MachineDB(Base):
    __tablename__ = "machines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    type = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)
    
    # Добавляем связь с карточками
    cards = relationship("CardDB", back_populates="machine")

class EmployeeDB(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer)
    full_name = Column(String(255))
    username = Column(String(255))
    role_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)
    added_by = Column(Integer)
    is_active = Column(Boolean, default=True)

class PartDB(Base):
    __tablename__ = "parts"

    id = Column(Integer, primary_key=True, index=True)
    drawing_number = Column(String(255), unique=True, index=True)
    material = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    # is_active = Column(Boolean, default=True)

class LotDB(Base):
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True, index=True)
    lot_number = Column(String(255))
    part_id = Column(Integer, ForeignKey("parts.id"))
    created_at = Column(DateTime, default=datetime.now)
    # is_active = Column(Boolean, default=True)

    # Новые поля, добавленные ранее:
    order_manager_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    created_by_order_manager_at = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)
    initial_planned_quantity = Column(Integer, nullable=True)
    total_planned_quantity = Column(Integer, nullable=True)
    status = Column(String(50), nullable=False, default='new') # Статус лота

    # Добавляем обратную связь к BatchDB
    batches = relationship("BatchDB", back_populates="lot")
    # Добавляем связь с PartDB для удобства доступа (если еще нет)
    part = relationship("PartDB") # Без back_populates, если у PartDB нет обратной связи

class SetupDB(Base):
    __tablename__ = "setup_jobs"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"))
    machine_id = Column(Integer, ForeignKey("machines.id"))
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)
    planned_quantity = Column(Integer)
    status = Column(String(50))
    cycle_time = Column(Integer)
    lot_id = Column(Integer, ForeignKey("lots.id"))
    part_id = Column(Integer, ForeignKey("parts.id"))
    qa_date = Column(DateTime)
    qa_id = Column(Integer, ForeignKey("employees.id"))
    additional_quantity = Column(Integer, default=0)

    # Добавляем обратную связь к BatchDB
    batches = relationship("BatchDB", back_populates="setup_job")

class BatchDB(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    setup_job_id = Column(Integer, ForeignKey("setup_jobs.id"), nullable=True)
    lot_id = Column(Integer, ForeignKey("lots.id"), nullable=False)
    parent_batch_id = Column(Integer, ForeignKey("batches.id"), nullable=True)

    initial_quantity = Column(Integer, nullable=False) # Кол-во при создании (например, разница счетчика)
    operator_reported_quantity = Column(Integer, nullable=True) # Кол-во от оператора перед приемкой складом
    recounted_quantity = Column(Integer, nullable=True) # Кол-во, пересчитанное кладовщиком
    current_quantity = Column(Integer, nullable=False) # Актуальное кол-во ПОСЛЕ приемки/инспекции
    
    current_location = Column(String, nullable=False, default='production')

    operator_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    warehouse_employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    qc_inspector_id = Column(Integer, ForeignKey("employees.id"), nullable=True)

    batch_time = Column(DateTime, default=datetime.now)
    warehouse_received_at = Column(DateTime, nullable=True)
    qa_date = Column(DateTime, nullable=True)
    
    qc_comment = Column(Text, nullable=True)

    # Поля для расхождений при приемке складом
    discrepancy_absolute = Column(Integer, nullable=True)
    discrepancy_percentage = Column(Float, nullable=True) 
    admin_acknowledged_discrepancy = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # Связи
    lot = relationship("LotDB", back_populates="batches")
    setup_job = relationship("SetupDB", back_populates="batches")
    parent_batch = relationship("BatchDB", remote_side=[id], back_populates="child_batches")
    child_batches = relationship("BatchDB", back_populates="parent_batch")
    
    operator = relationship("EmployeeDB", foreign_keys=[operator_id])
    warehouse_employee = relationship("EmployeeDB", foreign_keys=[warehouse_employee_id])
    qc_inspector = relationship("EmployeeDB", foreign_keys=[qc_inspector_id])
    
    # Добавляем связь с карточкой
    card = relationship("CardDB", back_populates="batch", uselist=False)

class ReadingDB(Base):
    __tablename__ = "machine_readings"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"))
    machine_id = Column(Integer, ForeignKey("machines.id"))
    reading = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)

class CardDB(Base):
    """Модель для пластиковых карточек операторов"""
    __tablename__ = 'cards'
    
    card_number = Column(Integer, primary_key=True)  # номер на пластике (1-20)
    machine_id = Column(BigInteger, ForeignKey('machines.id'), primary_key=True)  # составной ключ
    status = Column(String(20), nullable=False, default='free')  # free, in_use, lost
    batch_id = Column(BigInteger, ForeignKey('batches.id'), nullable=True)
    last_event = Column(DateTime, nullable=False, default=func.now())
    
    # Отношения
    machine = relationship("MachineDB", back_populates="cards")
    batch = relationship("BatchDB", back_populates="card", uselist=False)
    
    __table_args__ = (
        CheckConstraint("status IN ('free', 'in_use', 'lost')", name='check_card_status'),
        Index('idx_cards_machine_status', 'machine_id', 'status'),
        Index('idx_cards_batch_id', 'batch_id'),
    )
