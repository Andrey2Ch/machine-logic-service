from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import httpx

router = APIRouter()


class StatusChangeEvent(BaseModel):
    type: str = Field('MACHINE_STATUS_CHANGED')
    machine_id: str
    machine_name: str
    from_status: str
    to_status: str
    occurred_at: str
    idle_time_minutes: Optional[float] = None
    cycle_time_minutes: Optional[float] = None
    program: Optional[str] = None
    edge_gateway_id: Optional[str] = None
    shop: Optional[str] = None
    idempotency_key: str


# In-memory subscribers (no DB). Each item: { url, filters: { statuses, machines, shops, roles } }
SUBSCRIBERS: List[Dict[str, Any]] = []


def _match_filters(evt: Dict[str, Any], filt: Dict[str, Any]) -> bool:
    statuses: List[str] = filt.get('statuses') or []
    machines: List[str] = filt.get('machines') or []
    shops: List[str] = filt.get('shops') or []
    roles: List[str] = filt.get('roles') or []

    if statuses and evt.get('to_status') not in statuses:
        return False
    if machines and evt.get('machine_id') not in machines and evt.get('machine_name') not in machines:
        return False
    if shops and (evt.get('shop') not in shops):
        return False
    # roles — зарезервировано под будущие расширения (привязка к пользователям)
    return True


@router.post('/events/machine-status', tags=['Events'])
async def machine_status_event(event: StatusChangeEvent):
    if event.type != 'MACHINE_STATUS_CHANGED':
        raise HTTPException(status_code=400, detail='Unsupported event type')

    delivered = 0
    payload = event.model_dump()

    targets: List[str] = []
    for sub in SUBSCRIBERS:
        url = sub.get('url')
        filt = sub.get('filters') or {}
        if not url:
            continue
        if _match_filters(payload, filt):
            targets.append(url)

    if targets:
        async with httpx.AsyncClient(timeout=5.0) as client:
            for url in targets:
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code < 500:
                        delivered += 1
                except Exception:
                    # не падаем, продолжаем
                    pass

    return {"ok": True, "delivered": delivered, "targets": len(targets)}


# ===== Subscriptions management (in-memory, minimal) =====
class SubscriptionFilters(BaseModel):
    statuses: Optional[List[str]] = None
    machines: Optional[List[str]] = None
    shops: Optional[List[str]] = None
    roles: Optional[List[str]] = None

class SubscriptionRequest(BaseModel):
    url: str
    filters: Optional[SubscriptionFilters] = None


@router.post('/events/subscribe', tags=['Events'])
async def subscribe(req: SubscriptionRequest):
    entry = {
        'url': req.url,
        'filters': (req.filters.model_dump() if req.filters else {})
    }
    # deduplicate by exact url+filters
    for s in SUBSCRIBERS:
        if s.get('url') == entry['url'] and (s.get('filters') or {}) == entry['filters']:
            return { 'ok': True, 'already': True, 'count': len(SUBSCRIBERS) }
    SUBSCRIBERS.append(entry)
    return { 'ok': True, 'count': len(SUBSCRIBERS) }


@router.get('/events/subscribers', tags=['Events'])
async def list_subscribers():
    return { 'ok': True, 'count': len(SUBSCRIBERS), 'items': SUBSCRIBERS }


@router.delete('/events/subscribe', tags=['Events'])
async def unsubscribe(url: str):
    before = len(SUBSCRIBERS)
    SUBSCRIBERS[:] = [s for s in SUBSCRIBERS if s.get('url') != url]
    return { 'ok': True, 'removed': before - len(SUBSCRIBERS), 'count': len(SUBSCRIBERS) }


