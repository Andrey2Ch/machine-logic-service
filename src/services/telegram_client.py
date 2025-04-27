import os
import logging
# Используем импорты aiogram
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties # <-- Импортируем DefaultBotProperties
from aiogram.exceptions import TelegramAPIError

logger = logging.getLogger(__name__)

# Инициализируем бота aiogram
bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
if not bot_token:
    logger.warning("TELEGRAM_BOT_TOKEN is not set. Telegram notifications will be disabled.")
    bot = None
else:
    # Используем новый способ задания parse_mode через DefaultBotProperties
    default_props = DefaultBotProperties(parse_mode='HTML')
    bot = Bot(token=bot_token, default=default_props)

async def send_telegram_message(chat_id: int, text: str):
    """Асинхронно отправляет сообщение в Telegram с использованием aiogram."""
    if not bot:
        logger.warning(f"Telegram bot (aiogram) not initialized. Cannot send message to {chat_id}.")
        return False

    try:
        # Используем метод send_message из aiogram
        await bot.send_message(
            chat_id=chat_id,
            text=text
            # parse_mode='HTML' # Уже установлен через default_props
        )
        logger.info(f"Sent Telegram message via aiogram to chat_id {chat_id}")
        return True
    except TelegramAPIError as e:
        # Используем исключение из aiogram
        logger.error(f"Telegram (aiogram) send error to chat_id {chat_id}: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending Telegram (aiogram) message to {chat_id}: {e}", exc_info=True)
        return False