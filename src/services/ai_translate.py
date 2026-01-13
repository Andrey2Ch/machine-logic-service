"""
AI Translation Service using Anthropic Claude
Перевод уведомлений на лету
"""
import os
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# Кэш переводов (простой in-memory)
_translation_cache: dict[str, str] = {}


async def translate_text(
    text: str,
    target_language: str = "he",
    context: Optional[str] = None
) -> str:
    """
    Переводит текст на целевой язык используя Claude.
    
    Args:
        text: Текст для перевода
        target_language: Код языка (he=иврит, en=английский, ru=русский)
        context: Дополнительный контекст (напр. "это уведомление для операторов станков")
    
    Returns:
        Переведённый текст
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set, returning original text")
        return text
    
    if not text or not text.strip():
        return text
    
    # Проверяем кэш
    cache_key = f"{target_language}:{text}"
    if cache_key in _translation_cache:
        return _translation_cache[cache_key]
    
    language_names = {
        "he": "Hebrew (иврит)",
        "en": "English",
        "ru": "Russian (русский)",
        "ar": "Arabic (арабский)"
    }
    
    target_lang_name = language_names.get(target_language, target_language)
    
    system_prompt = """You are a professional translator for industrial/manufacturing notifications.
Translate the text accurately, keeping:
- Machine names and technical terms as-is (SR-21, XD-38, etc.)
- Numbers and measurements unchanged
- Emoji if present
- Keep the message concise and natural in the target language

HEBREW GLOSSARY (use these specific terms when translating to Hebrew):
- Чертёж / Drawing number → פריט (part/item, NOT ציור)
- Партия / Lot → פק"ע (work order abbreviation, NOT קבוצה)
- Batch / Серия → מנה
- Брак / Defect → פסולים (NOT פגום)
- Общий брак по лоту → סך הכל פסולים בפק"ע
- Зафиксирован брак → התגלו פסולים
- Станок → מכונה
- Оператор → מפעיל
- Наладчик → כוון

Output ONLY the translated text, nothing else."""

    user_prompt = f"""Translate to {target_lang_name}:

{text}"""

    if context:
        user_prompt += f"\n\nContext: {context}"
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5",  # Новейшая Haiku (Oct 2025) - $1/$5 per 1M tokens
                    "max_tokens": 1024,
                    "system": system_prompt,
                    "messages": [
                        {"role": "user", "content": user_prompt}
                    ]
                }
            )
            
            if response.status_code != 200:
                logger.error(f"Anthropic API error: {response.status_code} - {response.text}")
                return text
            
            data = response.json()
            translated = data.get("content", [{}])[0].get("text", text)
            
            # Сохраняем в кэш
            _translation_cache[cache_key] = translated
            
            logger.info(f"Translated to {target_language}: '{text[:50]}...' -> '{translated[:50]}...'")
            return translated
            
    except Exception as e:
        logger.error(f"Translation error: {e}")
        return text


async def translate_notification(
    notification_text: str,
    user_language: str = "he"
) -> str:
    """
    Переводит уведомление на язык пользователя.
    
    Args:
        notification_text: Текст уведомления (обычно на русском)
        user_language: Язык пользователя
    
    Returns:
        Переведённый текст
    """
    # Русский — не переводим
    if user_language in ["ru", "rus", "russian"]:
        return notification_text
    
    return await translate_text(
        notification_text,
        target_language=user_language,
        context="This is a factory notification for machine operators/technicians"
    )


def clear_translation_cache():
    """Очищает кэш переводов"""
    global _translation_cache
    _translation_cache = {}
    logger.info("Translation cache cleared")
