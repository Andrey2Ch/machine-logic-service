from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Enum, Boolean, Float, Text, BigInteger, CheckConstraint, Index
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from .setup import SetupStatus
from ..database import Base
from sqlalchemy.sql import func

class AreaDB(Base):
    __tablename__ = "areas"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    code = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    machines = relationship("MachineDB", back_populates="area")

class MachineDB(Base):
    __tablename__ = "machines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255))
    type = Column(String(50))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=True)
    location_id = Column(Integer, ForeignKey("areas.id"), nullable=False)
    serial_number = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    display_order = Column(Integer, nullable=True)
    
    # Добавляем связь с карточками
    cards = relationship("CardDB", back_populates="machine")
    # Связь с area
    area = relationship("AreaDB", back_populates="machines")
    # Связь с материалами
    lot_materials = relationship("LotMaterialDB", back_populates="machine")

class EmployeeDB(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(Integer)
    full_name = Column(String(255))
    username = Column(String(255))
    role_id = Column(Integer)
    factory_number = Column(String(50), nullable=True, unique=True)  # Заводской номер оператора
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    added_by = Column(Integer)
    is_active = Column(Boolean, default=True)
    # Default area for UI filtering
    default_area_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    
    # Связи с материалами
    lot_materials_issued = relationship("LotMaterialDB", foreign_keys="LotMaterialDB.issued_by", back_populates="issued_by_employee")
    lot_materials_returned = relationship("LotMaterialDB", foreign_keys="LotMaterialDB.returned_by", back_populates="returned_by_employee")
    lot_materials_closed = relationship("LotMaterialDB", foreign_keys="LotMaterialDB.closed_by", back_populates="closed_by_employee")


class EmployeeAreaRoleDB(Base):
    __tablename__ = "employee_area_roles"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    area_id = Column(Integer, ForeignKey("areas.id"), nullable=False)
    role = Column(String(50), nullable=False)  # e.g., 'operator', 'machinist', 'qa'

    __table_args__ = (
        # Unique per (employee, area, role)
        CheckConstraint("length(role) > 0", name="check_employee_area_role_nonempty"),
    )

class PartDB(Base):
    __tablename__ = "parts"

    id = Column(Integer, primary_key=True, index=True)
    drawing_number = Column(String(255), unique=True, index=True)
    material = Column(Text, nullable=True)
    recommended_diameter = Column(Float, nullable=True)  # Рекомендованный диаметр в мм
    profile_type = Column(String(20), nullable=True, default='round')  # Тип профиля (round/hex/square)
    part_length = Column(Float, nullable=True)  # Длина детали в мм
    drawing_url = Column(Text, nullable=True)  # URL чертежа (Cloudinary)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    avg_cycle_time = Column(Integer, nullable=True)  # Среднее время цикла в секундах
    # is_active = Column(Boolean, default=True)

class LotDB(Base):
    __tablename__ = "lots"

    id = Column(Integer, primary_key=True, index=True)
    lot_number = Column(String(255))
    part_id = Column(Integer, ForeignKey("parts.id"))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # is_active = Column(Boolean, default=True)

    # Новые поля, добавленные ранее:
    order_manager_id = Column(Integer, ForeignKey("employees.id"), nullable=True)
    created_by_order_manager_at = Column(DateTime, nullable=True)
    due_date = Column(DateTime, nullable=True)
    initial_planned_quantity = Column(Integer, nullable=True)
    total_planned_quantity = Column(Integer, nullable=True)
    status = Column(String(50), nullable=False, default='new') # Статус лота
    assigned_machine_id = Column(Integer, ForeignKey("machines.id"), nullable=True)  # Назначенный станок (для статуса assigned)
    assigned_order = Column(Integer, nullable=True)  # Порядок в очереди на станке
    actual_diameter = Column(Float, nullable=True)  # Фактический диаметр материала (хранится в БД)
    actual_profile_type = Column(String(20), nullable=True, default='round')  # Фактический тип профиля (round, hexagon, square)

    # Добавляем обратную связь к BatchDB
    batches = relationship("BatchDB", back_populates="lot")
    # Добавляем связь с PartDB для удобства доступа (если еще нет)
    part = relationship("PartDB") # Без back_populates, если у PartDB нет обратной связи
    # Связь с материалами
    lot_materials = relationship("LotMaterialDB", back_populates="lot")
    
    # Временные атрибуты (не в БД, заполняются в endpoint для Kanban)
    machine_name = None  # Название станка (уже используется)
    actual_produced = None  # Текущее произведенное количество из machine_readings
    setup_status = None  # Статус активной наладки

class SetupDB(Base):
    __tablename__ = "setup_jobs"

    id = Column(Integer, primary_key=True, index=True)
    employee_id = Column(Integer, ForeignKey("employees.id"))
    machine_id = Column(Integer, ForeignKey("machines.id"))
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
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
    
    # Добавляем связи для доступа к данным станка и оператора
    machine = relationship("MachineDB")
    operator = relationship("EmployeeDB", foreign_keys=[employee_id])

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

    batch_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    warehouse_received_at = Column(DateTime, nullable=True)
    qa_date = Column(DateTime, nullable=True)
    
    qc_comment = Column(Text, nullable=True)

    # Поля для расхождений при приемке складом
    discrepancy_absolute = Column(Integer, nullable=True)
    discrepancy_percentage = Column(Float, nullable=True) 
    admin_acknowledged_discrepancy = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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
    setup_job_id = Column(Integer, ForeignKey("setup_jobs.id"), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class CardDB(Base):
    """Модель для пластиковых карточек операторов"""
    __tablename__ = 'cards'
    
    card_number = Column(Integer, primary_key=True)  # номер на пластике (1-20)
    machine_id = Column(BigInteger, ForeignKey('machines.id'), primary_key=True)  # составной ключ
    status = Column(String(20), nullable=False, default='free')  # free, in_use, lost
    batch_id = Column(BigInteger, ForeignKey('batches.id'), nullable=True)
    last_event = Column(DateTime, nullable=False, default=func.now())  # Оставляем PostgreSQL функцию для CardDB
    
    # Отношения
    machine = relationship("MachineDB", back_populates="cards")
    batch = relationship("BatchDB", back_populates="card", uselist=False)
    
    __table_args__ = (
        CheckConstraint("status IN ('free', 'in_use', 'lost')", name='check_card_status'),
        Index('idx_cards_machine_status', 'machine_id', 'status'),
        Index('idx_cards_batch_id', 'batch_id'),
    )

class MaterialTypeDB(Base):
    __tablename__ = "material_types"

    id = Column(Integer, primary_key=True, index=True)
    material_name = Column(String(100), unique=True, nullable=False)
    density_kg_per_m3 = Column(Float, nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class LotMaterialDB(Base):
    __tablename__ = "lot_materials"

    id = Column(Integer, primary_key=True, index=True)
    lot_id = Column(Integer, ForeignKey("lots.id", ondelete="CASCADE"), nullable=False)
    machine_id = Column(Integer, ForeignKey("machines.id", ondelete="SET NULL"), nullable=True)
    material_receipt_id = Column(Integer, nullable=True)  # будет FK позже
    material_type = Column(String(100), nullable=True)
    diameter = Column(Float, nullable=True)
    calculated_bars_needed = Column(Integer, nullable=True)
    calculated_weight_kg = Column(Float, nullable=True)
    issued_bars = Column(Integer, default=0)
    issued_weight_kg = Column(Float, nullable=True)
    issued_at = Column(DateTime, nullable=True)
    issued_by = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    returned_bars = Column(Integer, default=0)
    returned_weight_kg = Column(Float, nullable=True)
    returned_at = Column(DateTime, nullable=True)
    returned_by = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    defect_bars = Column(Integer, default=0)  # Бракованные/погнутые прутки
    # used_bars — это generated column в PostgreSQL, не добавлять в INSERT!
    # Вычисляется автоматически: issued_bars - returned_bars - defect_bars
    status = Column(String(20), default="pending")
    notes = Column(Text, nullable=True)
    closed_at = Column(DateTime, nullable=True)  # Дата закрытия записи кладовщиком
    closed_by = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)  # Кто закрыл
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    lot = relationship("LotDB", back_populates="lot_materials")
    machine = relationship("MachineDB", back_populates="lot_materials")
    issued_by_employee = relationship("EmployeeDB", foreign_keys=[issued_by], back_populates="lot_materials_issued")
    returned_by_employee = relationship("EmployeeDB", foreign_keys=[returned_by], back_populates="lot_materials_returned")
    closed_by_employee = relationship("EmployeeDB", foreign_keys=[closed_by], back_populates="lot_materials_closed")

    __table_args__ = (
        Index('idx_lot_materials_lot_id', 'lot_id'),
        Index('idx_lot_materials_machine_id', 'machine_id'),
        Index('idx_lot_materials_status', 'status'),
    )
    
    # Связь с операциями
    operations = relationship("MaterialOperationDB", back_populates="lot_material", cascade="all, delete-orphan")


class MaterialOperationDB(Base):
    """История операций с материалом (выдача, добавление, возврат)"""
    __tablename__ = "material_operations"

    id = Column(Integer, primary_key=True, index=True)
    lot_material_id = Column(Integer, ForeignKey("lot_materials.id", ondelete="CASCADE"), nullable=False)
    operation_type = Column(String(20), nullable=False)  # issue, add, return, correction
    quantity_bars = Column(Integer, nullable=False)  # положительное = выдача, отрицательное = возврат
    diameter = Column(Float, nullable=True)  # диаметр (для справки)
    performed_by = Column(Integer, ForeignKey("employees.id", ondelete="SET NULL"), nullable=True)
    performed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    # Отношения
    lot_material = relationship("LotMaterialDB", back_populates="operations")
    performer = relationship("EmployeeDB", foreign_keys=[performed_by])

    __table_args__ = (
        Index('idx_material_operations_lot_material_id', 'lot_material_id'),
        Index('idx_material_operations_performed_at', 'performed_at'),
        CheckConstraint("operation_type IN ('issue', 'add', 'return', 'correction')", name='check_operation_type'),
    )
