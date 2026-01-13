"""
API для AI перевода уведомлений
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from src.services.ai_translate import translate_text, translate_notification, clear_translation_cache

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/translate", tags=["Translation"])


class TranslateRequest(BaseModel):
    text: str
    target_language: str = "he"  # he=иврит, en=английский, ru=русский, ar=арабский
    context: Optional[str] = None


class TranslateResponse(BaseModel):
    original: str
    translated: str
    target_language: str


class NotificationTranslateRequest(BaseModel):
    notification_text: str
    user_language: str = "he"


@router.post("/text", response_model=TranslateResponse)
async def translate_text_endpoint(request: TranslateRequest):
    """
    Переводит произвольный текст на указанный язык.
    Использует Claude AI для перевода.
    """
    try:
        translated = await translate_text(
            text=request.text,
            target_language=request.target_language,
            context=request.context
        )
        
        return TranslateResponse(
            original=request.text,
            translated=translated,
            target_language=request.target_language
        )
    except Exception as e:
        logger.error(f"Translation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notification", response_model=TranslateResponse)
async def translate_notification_endpoint(request: NotificationTranslateRequest):
    """
    Переводит уведомление на язык пользователя.
    Оптимизирован для заводских уведомлений.
    """
    try:
        translated = await translate_notification(
            notification_text=request.notification_text,
            user_language=request.user_language
        )
        
        return TranslateResponse(
            original=request.notification_text,
            translated=translated,
            target_language=request.user_language
        )
    except Exception as e:
        logger.error(f"Notification translation error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/clear")
async def clear_cache_endpoint():
    """Очищает кэш переводов"""
    try:
        clear_translation_cache()
        return {"success": True, "message": "Translation cache cleared"}
    except Exception as e:
        logger.error(f"Cache clear error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
