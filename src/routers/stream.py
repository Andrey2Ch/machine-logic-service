"""
SSE (Server-Sent Events) Router

Предоставляет real-time стриминг данных для дашбордов.
Клиенты подключаются один раз и получают обновления автоматически.

Использование:
    const eventSource = new EventSource('/api/stream/dashboard');
    eventSource.onmessage = (e) => console.log(JSON.parse(e.data));
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from src.services.dashboard_collector import get_dashboard_state

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stream", tags=["Stream"])

# ========== КОНФИГУРАЦИЯ ==========

SSE_RETRY_MS = 5000  # Клиент переподключится через 5 сек при разрыве
HEARTBEAT_INTERVAL_SEC = 30  # Keep-alive каждые 30 сек


# ========== SSE GENERATOR ==========

async def dashboard_event_generator(request: Request) -> AsyncGenerator[str, None]:
    """
    Генератор SSE событий для дашборда.
    
    Отправляет:
    - data: JSON с данными при каждом обновлении
    - :heartbeat при отсутствии данных (keep-alive)
    """
    state = get_dashboard_state()
    last_sent_timestamp = 0
    last_heartbeat = time.time()
    
    client_ip = request.client.host if request.client else "unknown"
    logger.info(f"🔌 SSE client connected: {client_ip}")
    
    # Отправляем retry интервал
    yield f"retry: {SSE_RETRY_MS}\n\n"
    
    try:
        while True:
            # Проверяем отключение клиента
            if await request.is_disconnected():
                logger.info(f"🔌 SSE client disconnected: {client_ip}")
                break
            
            current_data = state.get_data()
            current_timestamp = state.last_update
            
            # Отправляем данные если они обновились
            if current_timestamp > last_sent_timestamp and current_data:
                data_json = json.dumps(current_data, ensure_ascii=False)
                yield f"data: {data_json}\n\n"
                last_sent_timestamp = current_timestamp
                last_heartbeat = time.time()
            
            # Heartbeat для keep-alive
            elif time.time() - last_heartbeat > HEARTBEAT_INTERVAL_SEC:
                yield ": heartbeat\n\n"
                last_heartbeat = time.time()
            
            # Ждём перед следующей проверкой
            await asyncio.sleep(1)
            
    except asyncio.CancelledError:
        logger.info(f"🔌 SSE connection cancelled: {client_ip}")
    except Exception as e:
        logger.error(f"❌ SSE error for {client_ip}: {e}")
    finally:
        logger.debug(f"🔌 SSE generator cleanup: {client_ip}")


# ========== ENDPOINTS ==========

@router.get("/dashboard")
async def stream_dashboard(request: Request):
    """
    SSE endpoint для дашборда.
    
    Возвращает поток событий с данными о станках, utilization, setup times.
    
    Клиент подключается один раз:
    ```javascript
    const es = new EventSource('/api/stream/dashboard');
    es.onmessage = (e) => {
        const data = JSON.parse(e.data);
        updateDashboard(data);
    };
    ```
    """
    return StreamingResponse(
        dashboard_event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Отключает буферизацию nginx
            "Access-Control-Allow-Origin": "*",
        }
    )


@router.get("/status")
async def stream_status():
    """
    Статус сервиса стриминга.
    Полезно для диагностики.
    """
    state = get_dashboard_state()
    
    return {
        "service": "dashboard-stream",
        "status": "running" if state.is_collecting else "stopped",
        "last_update": state.last_update,
        "last_update_ago_sec": round(time.time() - state.last_update, 1) if state.last_update else None,
        "error": state.error,
        "machines_count": len(state.data.get("machines", [])) if state.data else 0,
    }
