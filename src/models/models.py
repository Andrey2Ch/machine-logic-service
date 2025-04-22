from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from .setup import SetupStatus
from ..database import Base

class MachineDB(Base):
    __tablename__ = "machines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    type = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)

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
    drawing_number = Column(String(255))
    description = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)

class LotDB(Base):
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True, index=True)
    lot_number = Column(String(255))
    part_id = Column(Integer, ForeignKey("parts.id"))
    created_at = Column(DateTime, default=datetime.now)
    is_active = Column(Boolean, default=True)

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

class BatchDB(Base):
    __tablename__ = "batches"

    id = Column(Integer, primary_key=True, index=True)
    setup_job_id = Column(Integer, ForeignKey("setup_jobs.id"))
    lot_id = Column(Integer, ForeignKey("lots.id"))
    initial_quantity = Column(Integer)
    current_quantity = Column(Integer)
    recounted_quantity = Column(Integer)
    current_location = Column(String(255))
    operator_id = Column(Integer, ForeignKey("employees.id"))
    parent_batch_id = Column(Integer, ForeignKey("batches.id"))
    batch_time = Column(DateTime)
    created_at = Column(DateTime, default=datetime.now)

class ReadingDB(Base):
    __tablename__ = "machine_readings"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"))
    machine_id = Column(Integer, ForeignKey("machines.id"))
    reading = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)
