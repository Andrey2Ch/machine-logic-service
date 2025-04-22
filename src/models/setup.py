from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from typing import Optional, List

class SetupStatus(str, Enum):
    """Статусы наладки"""
    CREATED = 'created'      # Создана наладчиком
    PENDING_QC = 'pending_qc'# На проверке ОТК
    ALLOWED = 'allowed'      # Разрешена ОТК
    STARTED = 'started'      # Запущена (после ввода нулевых показаний)
    COMPLETED = 'completed'  # Завершена
    QUEUED = 'queued'       # В очереди
    IDLE = 'idle'           # Станок простаивает

class Reading(BaseModel):
    """Модель показаний счетчика"""
    operator_id: int
    machine_id: int
    value: int = Field(ge=0)  # Значение должно быть >= 0
    timestamp: datetime = Field(default_factory=datetime.now)

class Setup(BaseModel):
    """Модель наладки"""
    id: Optional[int] = None
    machine_id: int
    employee_id: int  # Наладчик
    part_id: int
    lot_id: int
    planned_quantity: int
    status: SetupStatus = SetupStatus.CREATED
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.now)
    qa_id: Optional[int] = None  # ID сотрудника ОТК
    cycle_time: Optional[int] = None  # Время цикла в секундах

    class Config:
        use_enum_values = True

    def process_reading(self, reading: Reading) -> tuple[bool, str]:
        """
        Обработка показаний счетчика
        Returns: (success, message)
        """
        try:
            if reading.value == 0:
                return self._process_zero_reading(reading)
            else:
                return self._process_non_zero_reading(reading)
        except Exception as e:
            return False, f"Ошибка обработки показаний: {str(e)}"

    def _process_zero_reading(self, reading: Reading) -> tuple[bool, str]:
        """Обработка нулевых показаний"""
        if self.status in [SetupStatus.CREATED, SetupStatus.ALLOWED]:
            self.status = SetupStatus.STARTED
            self.start_time = datetime.now()
            return True, "Наладка активирована"
        return True, "Нулевые показания сохранены"

    def _process_non_zero_reading(self, reading: Reading) -> tuple[bool, str]:
        """Обработка ненулевых показаний"""
        warning_message = ""

        # Проверка достижения плана
        if reading.value >= self.planned_quantity:
            warning_message = "⚠️ Достигнуто плановое количество!"

        if self.status == SetupStatus.STARTED:
            return True, warning_message if warning_message else "Показания сохранены"
        elif self.status in [SetupStatus.CREATED, SetupStatus.ALLOWED]:
            self.status = SetupStatus.STARTED
            self.start_time = datetime.now()
            return True, f"Наладка активирована. {warning_message}"
        else:
            return False, f"Недопустимый статус наладки: {self.status}"

    def can_change_status(self, new_status: SetupStatus) -> bool:
        """Проверка возможности изменения статуса"""
        allowed_transitions = {
            SetupStatus.CREATED: [SetupStatus.PENDING_QC, SetupStatus.STARTED, SetupStatus.COMPLETED, SetupStatus.QUEUED],
            SetupStatus.PENDING_QC: [SetupStatus.ALLOWED],
            SetupStatus.ALLOWED: [SetupStatus.STARTED, SetupStatus.COMPLETED],
            SetupStatus.STARTED: [SetupStatus.COMPLETED],
            SetupStatus.QUEUED: [SetupStatus.CREATED],
        }
        return new_status in allowed_transitions.get(self.status, [])