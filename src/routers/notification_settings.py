"""
API для управления настройками уведомлений WhatsApp/Telegram
"""
import logging
from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from pydantic import BaseModel

from src.database import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/notifications", tags=["Notification Settings"])


class NotificationSettingResponse(BaseModel):
    id: int
    notification_type: str
    display_name: str
    description: Optional[str]
    category: str
    enabled_machinists: bool
    enabled_operators: bool
    enabled_qa: bool
    enabled_admin: bool
    enabled_telegram: bool
    
    class Config:
        from_attributes = True


class NotificationSettingUpdate(BaseModel):
    enabled_machinists: Optional[bool] = None
    enabled_operators: Optional[bool] = None
    enabled_qa: Optional[bool] = None
    enabled_admin: Optional[bool] = None
    enabled_telegram: Optional[bool] = None


class BulkUpdateItem(BaseModel):
    notification_type: str
    enabled_machinists: Optional[bool] = None
    enabled_operators: Optional[bool] = None
    enabled_qa: Optional[bool] = None
    enabled_admin: Optional[bool] = None
    enabled_telegram: Optional[bool] = None


@router.get("/settings", response_model=List[NotificationSettingResponse])
async def get_notification_settings(db: Session = Depends(get_db_session)):
    """Получить все настройки уведомлений"""
    try:
        result = db.execute(text("""
            SELECT id, notification_type, display_name, description, category,
                   enabled_machinists, enabled_operators, enabled_qa, 
                   enabled_admin, enabled_telegram
            FROM notification_settings
            ORDER BY category, display_name
        """))
        
        rows = result.fetchall()
        return [
            NotificationSettingResponse(
                id=row.id,
                notification_type=row.notification_type,
                display_name=row.display_name,
                description=row.description,
                category=row.category,
                enabled_machinists=row.enabled_machinists,
                enabled_operators=row.enabled_operators,
                enabled_qa=row.enabled_qa,
                enabled_admin=row.enabled_admin,
                enabled_telegram=row.enabled_telegram
            )
            for row in rows
        ]
    except Exception as e:
        logger.error(f"Error fetching notification settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/settings/{notification_type}")
async def update_notification_setting(
    notification_type: str,
    update: NotificationSettingUpdate,
    db: Session = Depends(get_db_session)
):
    """Обновить настройки конкретного уведомления"""
    try:
        # Собираем только те поля, которые были переданы
        updates = []
        params = {"notification_type": notification_type}
        
        if update.enabled_machinists is not None:
            updates.append("enabled_machinists = :enabled_machinists")
            params["enabled_machinists"] = update.enabled_machinists
            
        if update.enabled_operators is not None:
            updates.append("enabled_operators = :enabled_operators")
            params["enabled_operators"] = update.enabled_operators
            
        if update.enabled_qa is not None:
            updates.append("enabled_qa = :enabled_qa")
            params["enabled_qa"] = update.enabled_qa
            
        if update.enabled_admin is not None:
            updates.append("enabled_admin = :enabled_admin")
            params["enabled_admin"] = update.enabled_admin
            
        if update.enabled_telegram is not None:
            updates.append("enabled_telegram = :enabled_telegram")
            params["enabled_telegram"] = update.enabled_telegram
        
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        query = f"""
            UPDATE notification_settings 
            SET {', '.join(updates)}
            WHERE notification_type = :notification_type
            RETURNING id, notification_type, display_name
        """
        
        result = db.execute(text(query), params)
        row = result.fetchone()
        
        if not row:
            raise HTTPException(status_code=404, detail="Notification type not found")
        
        db.commit()
        logger.info(f"Updated notification settings for {notification_type}")
        
        return {"success": True, "notification_type": notification_type}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating notification settings: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/settings/bulk-update")
async def bulk_update_notification_settings(
    updates: List[BulkUpdateItem],
    db: Session = Depends(get_db_session)
):
    """Массовое обновление настроек уведомлений"""
    try:
        updated_count = 0
        
        for item in updates:
            update_parts = []
            params = {"notification_type": item.notification_type}
            
            if item.enabled_machinists is not None:
                update_parts.append("enabled_machinists = :enabled_machinists")
                params["enabled_machinists"] = item.enabled_machinists
                
            if item.enabled_operators is not None:
                update_parts.append("enabled_operators = :enabled_operators")
                params["enabled_operators"] = item.enabled_operators
                
            if item.enabled_qa is not None:
                update_parts.append("enabled_qa = :enabled_qa")
                params["enabled_qa"] = item.enabled_qa
                
            if item.enabled_admin is not None:
                update_parts.append("enabled_admin = :enabled_admin")
                params["enabled_admin"] = item.enabled_admin
                
            if item.enabled_telegram is not None:
                update_parts.append("enabled_telegram = :enabled_telegram")
                params["enabled_telegram"] = item.enabled_telegram
            
            if update_parts:
                query = f"""
                    UPDATE notification_settings 
                    SET {', '.join(update_parts)}
                    WHERE notification_type = :notification_type
                """
                result = db.execute(text(query), params)
                if result.rowcount > 0:
                    updated_count += 1
        
        db.commit()
        logger.info(f"Bulk updated {updated_count} notification settings")
        
        return {"success": True, "updated_count": updated_count}
        
    except Exception as e:
        db.rollback()
        logger.error(f"Error in bulk update: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === Функция для проверки настроек при отправке ===

async def is_notification_enabled(
    db: Session,
    notification_type: str,
    channel: str  # 'machinists', 'operators', 'qa', 'admin', 'telegram'
) -> bool:
    """
    Проверяет, включено ли уведомление для данного канала.
    Используется перед отправкой уведомлений.
    """
    try:
        column_name = f"enabled_{channel}"
        if column_name not in ['enabled_machinists', 'enabled_operators', 
                               'enabled_qa', 'enabled_admin', 'enabled_telegram']:
            return True  # По умолчанию разрешаем
        
        result = db.execute(text(f"""
            SELECT {column_name} 
            FROM notification_settings 
            WHERE notification_type = :notification_type
        """), {"notification_type": notification_type})
        
        row = result.fetchone()
        if row:
            return bool(getattr(row, column_name, True))
        
        # Если настройка не найдена, разрешаем по умолчанию
        return True
        
    except Exception as e:
        logger.warning(f"Error checking notification setting: {e}")
        return True  # При ошибке разрешаем



