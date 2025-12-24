"""
Клиент для синхронизации с MTConnect API.

Отвечает за корректировку счётчика деталей в MTConnect
при записи показаний оператором или событиях ОТК.
"""

import os
import logging
import httpx

logger = logging.getLogger(__name__)

# URL MTConnect API - определяем в зависимости от окружения
# Production: https://mtconnect-core-production.up.railway.app
MTCONNECT_API_URL = os.getenv('MTCONNECT_API_URL', 'https://mtconnect-core-production.up.railway.app')

logger.info(f"MTConnect API URL: {MTCONNECT_API_URL}")


async def sync_counter_to_mtconnect(machine_name: str, desired_production: int) -> bool:
    """
    Синхронизирует счётчик деталей в MTConnect с указанным значением.
    
    Вызывает POST /api/counters/set на MTConnect cloud-api,
    который корректирует basePartCount так, чтобы productionPartCount
    стал равен desired_production.
    
    Args:
        machine_name: Имя станка (machineId в MTConnect), например "SR-26"
        desired_production: Желаемое значение счётчика деталей
        
    Returns:
        True если синхронизация успешна, False в противном случае
    """
    url = f"{MTCONNECT_API_URL}/api/counters/set"
    payload = {
        "machineId": machine_name,
        "desiredProduction": desired_production
    }
    
    try:
        logger.info(f"🔄 MTConnect sync: machine={machine_name}, desiredProduction={desired_production}")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            
            if response.status_code == 200 or response.status_code == 201:
                result = response.json()
                logger.info(f"✅ MTConnect sync success: {result}")
                return True
            else:
                logger.warning(f"⚠️ MTConnect sync failed: status={response.status_code}, response={response.text}")
                return False
                
    except httpx.ConnectError as e:
        logger.warning(f"⚠️ MTConnect API not available: {e}")
        return False
    except httpx.TimeoutException as e:
        logger.warning(f"⚠️ MTConnect API timeout: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ MTConnect sync error: {e}", exc_info=True)
        return False


async def reset_counter_on_qa_approval(machine_name: str) -> bool:
    """
    Сбрасывает счётчик деталей в MTConnect на 0 при разрешении ОТК.
    
    Вызывается когда статус наладки меняется на 'allowed'.
    
    Args:
        machine_name: Имя станка (machineId в MTConnect)
        
    Returns:
        True если сброс успешен, False в противном случае
    """
    logger.info(f"🔄 Resetting MTConnect counter to 0 for machine {machine_name} (QA approval)")
    return await sync_counter_to_mtconnect(machine_name, 0)

