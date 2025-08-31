## Machine Status Change Events (MLS)

Purpose: accept machine status change events from Cloud API and fan-out to subscribers (e.g., TG_bot) with filters.

### 1) Event payload (Cloud → MLS)
POST `/events/machine-status`

```json
{
  "type": "MACHINE_STATUS_CHANGED",
  "machine_id": "SR-32",
  "machine_name": "SR-32",
  "from_status": "working",
  "to_status": "idle",
  "occurred_at": "2025-07-01T10:00:00Z",
  "idle_time_minutes": 17,
  "cycle_time_minutes": 0.8,
  "program": "125-879",
  "edge_gateway_id": "edge-1",
  "shop": "turning",
  "idempotency_key": "SR-32|working->idle|2025-07-01T10:00"
}
```

Notes:
- `type` must be `MACHINE_STATUS_CHANGED`.
- Optional fields can be omitted.
- MLS does not persist payload by design in this minimal setup.

### 2) Subscriptions (in-memory)

- POST `/events/subscribe`
  ```json
  {
    "url": "http://localhost:8085/webhooks/mls",
    "filters": {
      "statuses": ["idle", "working", "breakdown", "setup"],
      "machines": [],
      "shops": [],
      "roles": []
    }
  }
  ```

- GET `/events/subscribers`

- DELETE `/events/subscribe?url=http://localhost:8085/webhooks/mls`

Filtering:
- `statuses`: match by `to_status`
- `machines`: match by `machine_id` or `machine_name`
- `shops`, `roles`: reserved for future

### 3) TG_bot local webhook example

Minimal FastAPI app (example):
```python
from fastapi import FastAPI, Request
app = FastAPI()

@app.post("/webhooks/mls")
async def mls(req: Request):
    payload = await req.json()
    print("MLS EVENT:", payload)
    return {"ok": True}
```

Run locally:
```
uvicorn app_webhook:app --host 0.0.0.0 --port 8085
```

### 4) Cloud API flags

Cloud API sends events only if all are set:
- `SEND_STATUS_EVENTS=true`
- `MACHINE_LOGIC_API_ENABLED=true`
- `MACHINE_LOGIC_SERVICE_INTERNAL_URL` (e.g., `http://localhost:8000`)

No UI or DB changes are required. Events are sent asynchronously after `GET /api/machines` updates internal snapshot state.


