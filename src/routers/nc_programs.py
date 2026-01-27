"""
Minimal CNC Program Vault API (MVP).

Storage:
- Railway Volume (similar to drawings.py) under /app/programs/blobs
- Files are deduplicated by sha256; storage_key points to blob path.

Swiss-type requirement:
- Each revision must include 2 files: role=main and role=sub
  (No ZIP. Download endpoints serve each file separately.)
"""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nc-programs", tags=["NC Programs"])

# IMPORTANT (Railway Volume):
# Drawings are stored on a Volume mounted at /app/drawings.
# To avoid losing NC blobs on container restarts, store them under the same Volume by default:
#   /app/drawings/programs/blobs/<sha256>
#
# You can override base dir via PROGRAM_VAULT_DIR if needed.
PROGRAM_VAULT_BASE_DIR = Path(os.environ.get("PROGRAM_VAULT_DIR") or "/app/drawings")
PROGRAMS_DIR = PROGRAM_VAULT_BASE_DIR / "programs"
BLOBS_DIR = PROGRAMS_DIR / "blobs"
BLOBS_DIR.mkdir(parents=True, exist_ok=True)

# ------------------------------------------------------------
# Channel model (v2)
#
# We use "role" column in nc_program_revision_files as a channel key:
# - legacy (already stored in DB): main/sub
# - new: ch1/ch2/ch3/... and nc (single-file)
#
# API returns normalized channel keys:
# - main -> ch1
# - sub  -> ch2
# ------------------------------------------------------------

LEGACY_TO_CHANNEL: Dict[str, str] = {"main": "ch1", "sub": "ch2"}
CHANNEL_TO_LEGACY: Dict[str, str] = {"ch1": "main", "ch2": "sub"}


def _normalize_channel_key(key: str) -> str:
    k = (key or "").strip().lower()
    return LEGACY_TO_CHANNEL.get(k, k)


def _candidates_for_role_or_channel(key: str) -> List[str]:
    """
    For DB lookups we accept both legacy and new keys.
    """
    k = (key or "").strip().lower()
    n = _normalize_channel_key(k)
    candidates = []
    if k:
        candidates.append(k)
    if n and n not in candidates:
        candidates.append(n)
    legacy = CHANNEL_TO_LEGACY.get(n)
    if legacy and legacy not in candidates:
        candidates.append(legacy)
    return candidates


def _default_profile_channels() -> List[Dict[str, str]]:
    # Shop default for Swiss-like machines: ch1=main, ch2=sub
    return [{"key": "ch1", "label": "main"}, {"key": "ch2", "label": "sub"}]


def _load_machine_type_profile(db: Session, machine_type: str) -> List[Dict[str, str]]:
    mt = (machine_type or "").strip()
    if not mt:
        return _default_profile_channels()

    row = db.execute(
        text(
            """
            select channels
            from nc_machine_type_profiles
            where machine_type = :machine_type
            limit 1
            """
        ),
        {"machine_type": mt},
    ).fetchone()
    if not row:
        return _default_profile_channels()

    channels = row.channels
    if not isinstance(channels, list):
        return _default_profile_channels()

    out: List[Dict[str, str]] = []
    for c in channels:
        if not isinstance(c, dict):
            continue
        key = _normalize_channel_key(str(c.get("key") or ""))
        label = str(c.get("label") or key)
        if not key:
            continue
        out.append({"key": key, "label": label})
    return out or _default_profile_channels()


def _required_channel_keys(profile_channels: List[Dict[str, str]]) -> List[str]:
    keys: List[str] = []
    for c in profile_channels or []:
        k = _normalize_channel_key(str(c.get("key") or ""))
        if k and k not in keys:
            keys.append(k)
    return keys or ["ch1", "ch2"]


class RevisionTextPayload(BaseModel):
    # channels: {"ch1": "O0001...\n...", "ch2": "..."}  OR legacy {"main": "...", "sub": "..."}
    channels: Dict[str, str]
    note: Optional[str] = None
    created_by_employee_id: Optional[int] = None


class NcProgramVaultSettingsPayload(BaseModel):
    default_history_limit: int
    updated_by_employee_id: Optional[int] = None


class NcMachineTypeProfilePayload(BaseModel):
    channels: List[Dict[str, str]]
    updated_by_employee_id: Optional[int] = None


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get_default_history_limit(db: Session) -> int:
    row = db.execute(
        text(
            """
            select value
            from app_settings
            where key = 'nc_program_vault.default_history_limit'
            limit 1
            """
        )
    ).fetchone()
    if not row:
        return 5
    val = getattr(row, "value", None)
    try:
        n = int(val)
        return max(1, min(100, n))
    except Exception:
        return 5


def _ensure_program_exists(db: Session, program_id: int) -> Dict[str, Any]:
    row = db.execute(
        text("""
            select id, part_id, machine_type, program_kind
            from nc_programs
            where id = :program_id
            limit 1
        """),
        {"program_id": program_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Программа не найдена")
    return dict(row)


@router.get("/parts/resolve", summary="Найти деталь по drawing_number (семейство)")
def resolve_part_by_drawing_number(
    drawing_number: str,
    db: Session = Depends(get_db_session),
):
    """
    Resolve Part ID(s) by drawing_number.

    Note: drawing_number in your system is the family number (e.g. "1002-11").
    """
    dn = (drawing_number or "").strip()
    if not dn:
        raise HTTPException(status_code=400, detail="drawing_number обязателен")

    # Try exact first, then prefix match for variants:
    # - if catalog stores "1002-11-1" but request is "1002-11", we still want to resolve.
    # - we keep it deterministic: exact matches first, then prefix matches.
    rows = db.execute(
        text("""
            select id, drawing_number, description
            from parts
            where trim(drawing_number) = :drawing_number
               or trim(drawing_number) like :drawing_prefix
            order by
                case when trim(drawing_number) = :drawing_number then 0 else 1 end,
                id asc
        """),
        {"drawing_number": dn, "drawing_prefix": f"{dn}-%"},
    ).mappings().all()

    return {"drawing_number": dn, "parts": [dict(r) for r in rows]}


@router.post("/parts/ensure", summary="Создать (или получить) деталь по drawing_number")
def ensure_part_by_drawing_number(
    drawing_number: str = Form(...),
    description: Optional[str] = Form(None),
    db: Session = Depends(get_db_session),
):
    """
    Idempotent: creates a minimal Part row if it doesn't exist yet.
    In your schema the only required field is parts.drawing_number.
    """
    dn = (drawing_number or "").strip()
    if not dn:
        raise HTTPException(status_code=400, detail="drawing_number обязателен")

    try:
        created = db.execute(
            text("""
                insert into parts (drawing_number, description, created_at)
                values (:drawing_number, :description, now())
                on conflict (drawing_number) do nothing
                returning id
            """),
            {"drawing_number": dn, "description": description},
        ).fetchone()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка создания детали: {e}")

    if created and getattr(created, "id", None):
        part_id = int(created.id)
        was_created = True
    else:
        row = db.execute(
            text("select id from parts where drawing_number = :drawing_number limit 1"),
            {"drawing_number": dn},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Не удалось создать/получить деталь")
        part_id = int(row.id)
        was_created = False

    return {"part_id": part_id, "drawing_number": dn, "created": was_created}


@router.get("/settings", summary="Настройки Program Vault (public)")
def get_nc_program_vault_settings(db: Session = Depends(get_db_session)):
    return {"default_history_limit": _get_default_history_limit(db)}


@router.put("/settings", summary="Обновить настройки Program Vault")
def update_nc_program_vault_settings(payload: NcProgramVaultSettingsPayload, db: Session = Depends(get_db_session)):
    limit = int(payload.default_history_limit)
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="default_history_limit должен быть в диапазоне 1..100")

    try:
        db.execute(
            text(
                """
                insert into app_settings (key, value, updated_at, updated_by_employee_id)
                values ('nc_program_vault.default_history_limit', to_jsonb(:limit), now(), :updated_by)
                on conflict (key) do update
                set value = excluded.value,
                    updated_at = excluded.updated_at,
                    updated_by_employee_id = excluded.updated_by_employee_id
                """
            ),
            {"limit": limit, "updated_by": payload.updated_by_employee_id},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения настроек: {e}")

    return {"default_history_limit": limit}


@router.get("/profiles", summary="Список профилей каналов по machine_type")
def list_nc_machine_type_profiles(db: Session = Depends(get_db_session)):
    rows = db.execute(
        text(
            """
            select machine_type, channels, created_at, updated_at
            from nc_machine_type_profiles
            order by machine_type asc
            """
        )
    ).mappings().all()
    return {"profiles": [dict(r) for r in rows]}


@router.put("/profiles/{machine_type}", summary="Создать/обновить профиль каналов для machine_type")
def upsert_nc_machine_type_profile(
    machine_type: str,
    payload: NcMachineTypeProfilePayload,
    db: Session = Depends(get_db_session),
):
    mt = (machine_type or "").strip()
    if not mt:
        raise HTTPException(status_code=400, detail="machine_type обязателен")

    # Basic validation for channels payload.
    channels_in = payload.channels or []
    normalized: List[Dict[str, str]] = []
    seen: set[str] = set()
    for c in channels_in:
        if not isinstance(c, dict):
            continue
        key = _normalize_channel_key(str(c.get("key") or ""))
        label = str(c.get("label") or key)
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append({"key": key, "label": label})

    if not normalized:
        raise HTTPException(status_code=400, detail="channels должен содержать хотя бы один канал")

    try:
        db.execute(
            text(
                """
                insert into nc_machine_type_profiles (machine_type, channels, created_at, updated_at)
                values (:machine_type, :channels::jsonb, now(), now())
                on conflict (machine_type) do update
                set channels = excluded.channels,
                    updated_at = now()
                """
            ),
            {"machine_type": mt, "channels": json.dumps(normalized)},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения профиля: {e}")

    return {"machine_type": mt, "channels": normalized}


@router.delete("/profiles/{machine_type}", summary="Удалить профиль каналов для machine_type")
def delete_nc_machine_type_profile(machine_type: str, db: Session = Depends(get_db_session)):
    mt = (machine_type or "").strip()
    if not mt:
        raise HTTPException(status_code=400, detail="machine_type обязателен")

    try:
        res = db.execute(
            text("delete from nc_machine_type_profiles where machine_type = :machine_type"),
            {"machine_type": mt},
        )
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка удаления профиля: {e}")

    # res.rowcount may be None depending on driver; don't hard fail.
    return {"ok": True, "machine_type": mt}


@router.get("/parts/{part_id}/list", summary="Список CNC программ по детали (последняя ревизия = текущая)")
def list_programs_for_part(
    part_id: int,
    machine_type: Optional[str] = None,
    db: Session = Depends(get_db_session),
):
    """
    Returns programs for a part with their current revision (max rev_number) and files (main/sub) if present.
    """
    params: Dict[str, Any] = {"part_id": part_id}
    mt_filter = ""
    if machine_type:
        mt_filter = "and p.machine_type = :machine_type"
        params["machine_type"] = machine_type

    rows = db.execute(
        text(f"""
            select
                p.id as program_id,
                p.part_id,
                p.machine_type,
                p.program_kind,
                p.title,
                p.comment,
                r.id as revision_id,
                r.rev_number,
                r.note as revision_note,
                r.created_at as revision_created_at,
                rf.role as file_role,
                fb.id as file_id,
                fb.sha256,
                fb.size_bytes,
                fb.original_filename,
                fb.created_at as file_created_at
            from nc_programs p
            left join lateral (
                select id, rev_number, note, created_at
                from nc_program_revisions
                where program_id = p.id
                order by rev_number desc
                limit 1
            ) r on true
            left join nc_program_revision_files rf on rf.revision_id = r.id
            left join file_blobs fb on fb.id = rf.file_id
            where p.part_id = :part_id
            {mt_filter}
            order by p.id desc
        """),
        params,
    ).mappings().all()

    # Preload machine_type profiles (small count: per part)
    machine_types: List[str] = []
    for r in rows:
        mt = (r.get("machine_type") or "").strip()
        if mt and mt not in machine_types:
            machine_types.append(mt)
    profiles_by_machine_type: Dict[str, List[Dict[str, str]]] = {
        mt: _load_machine_type_profile(db, mt) for mt in machine_types
    }

    # Assemble into { program -> current_revision -> files{ch1,ch2,...} }
    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        pid = int(row["program_id"])
        prog = out.get(pid)
        if not prog:
            mt = row["machine_type"]
            profile_channels = profiles_by_machine_type.get((mt or "").strip()) or _default_profile_channels()
            prog = {
                "program_id": pid,
                "part_id": row["part_id"],
                "machine_type": row["machine_type"],
                "program_kind": row["program_kind"],
                "title": row["title"],
                "comment": row["comment"],
                "channels_profile": profile_channels,
                "current_revision": None,
            }
            out[pid] = prog

        if row["revision_id"] is None:
            continue

        if prog["current_revision"] is None:
            required_keys = _required_channel_keys(prog.get("channels_profile") or _default_profile_channels())
            prog["current_revision"] = {
                "revision_id": int(row["revision_id"]),
                "rev_number": int(row["rev_number"]),
                "note": row["revision_note"],
                "created_at": row["revision_created_at"].isoformat() if row["revision_created_at"] else None,
                "files": {k: None for k in required_keys},
            }

        if row["file_role"] and row["file_id"]:
            channel_key = _normalize_channel_key(row["file_role"])
            # If file contains extra channels beyond profile, keep it too.
            if channel_key and channel_key not in prog["current_revision"]["files"]:
                prog["current_revision"]["files"][channel_key] = None
            if channel_key:
                prog["current_revision"]["files"][channel_key] = {
                    "file_id": int(row["file_id"]),
                    "sha256": row["sha256"],
                    "size_bytes": int(row["size_bytes"]) if row["size_bytes"] is not None else None,
                    "original_filename": row["original_filename"],
                }

    return {
        "part_id": part_id,
        "programs": list(out.values()),
        "profiles_by_machine_type": profiles_by_machine_type,
    }


@router.post("/parts/{part_id}/create", summary="Создать (или получить) CNC программу по детали + machine_type")
def create_program(
    part_id: int,
    machine_type: str = Form("any"),
    program_kind: str = Form("main"),
    title: Optional[str] = Form(None),
    comment: Optional[str] = Form(None),
    created_by_employee_id: Optional[int] = Form(None),
    db: Session = Depends(get_db_session),
):
    """
    Idempotent create by unique(part_id, machine_type, program_kind).
    """
    machine_type = (machine_type or "any").strip() or "any"
    program_kind = (program_kind or "main").strip() or "main"

    # Ensure part exists (fail fast)
    part_exists = db.execute(
        text("select id from parts where id = :part_id limit 1"),
        {"part_id": part_id},
    ).fetchone()
    if not part_exists:
        raise HTTPException(status_code=404, detail="Деталь не найдена")

    try:
        created = db.execute(
            text("""
                insert into nc_programs (part_id, machine_type, program_kind, title, comment, created_by_employee_id)
                values (:part_id, :machine_type, :program_kind, :title, :comment, :created_by_employee_id)
                on conflict (part_id, machine_type, program_kind) do nothing
                returning id
            """),
            {
                "part_id": part_id,
                "machine_type": machine_type,
                "program_kind": program_kind,
                "title": title,
                "comment": comment,
                "created_by_employee_id": created_by_employee_id,
            },
        ).fetchone()
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка создания программы: {e}")

    if created and getattr(created, "id", None):
        program_id = int(created.id)
    else:
        row = db.execute(
            text("""
                select id
                from nc_programs
                where part_id = :part_id
                  and machine_type = :machine_type
                  and program_kind = :program_kind
                limit 1
            """),
            {"part_id": part_id, "machine_type": machine_type, "program_kind": program_kind},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=500, detail="Не удалось создать/получить программу")
        program_id = int(row.id)

    return {"program_id": program_id, "part_id": part_id, "machine_type": machine_type, "program_kind": program_kind}


@router.post("/programs/{program_id}/revisions", summary="Загрузить новую ревизию (2 файла: main+sub)")
async def upload_revision(
    program_id: int,
    file_main: UploadFile = File(...),
    file_sub: UploadFile = File(...),
    note: Optional[str] = Form(None),
    created_by_employee_id: Optional[int] = Form(None),
    db: Session = Depends(get_db_session),
):
    """
    Legacy upload endpoint (2 files: main/sub).

    We store files as channels:
    - main -> ch1
    - sub  -> ch2
    No ZIP. Files are stored separately and linked via role.
    """
    _ensure_program_exists(db, program_id)

    if not file_main.filename or not file_sub.filename:
        raise HTTPException(status_code=400, detail="Нужно загрузить оба файла: main и sub")

    main_bytes = await file_main.read()
    sub_bytes = await file_sub.read()

    if not main_bytes or not sub_bytes:
        raise HTTPException(status_code=400, detail="Пустой файл: main или sub")

    main_sha = _sha256_hex(main_bytes)
    sub_sha = _sha256_hex(sub_bytes)

    # Storage keys
    main_key = f"blobs/{main_sha}"
    sub_key = f"blobs/{sub_sha}"

    # Persist blobs to volume only if not already exist
    main_path = PROGRAMS_DIR / main_key
    sub_path = PROGRAMS_DIR / sub_key

    if not main_path.exists():
        main_path.parent.mkdir(parents=True, exist_ok=True)
        main_path.write_bytes(main_bytes)
    if not sub_path.exists():
        sub_path.parent.mkdir(parents=True, exist_ok=True)
        sub_path.write_bytes(sub_bytes)

    try:
        # Ensure file_blobs rows exist
        db.execute(
            text("""
                insert into file_blobs (sha256, size_bytes, storage_key, original_filename, mime_type)
                values (:sha256, :size_bytes, :storage_key, :original_filename, :mime_type)
                on conflict (sha256) do nothing
            """),
            {
                "sha256": main_sha,
                "size_bytes": len(main_bytes),
                "storage_key": main_key,
                "original_filename": file_main.filename,
                "mime_type": file_main.content_type,
            },
        )
        db.execute(
            text("""
                insert into file_blobs (sha256, size_bytes, storage_key, original_filename, mime_type)
                values (:sha256, :size_bytes, :storage_key, :original_filename, :mime_type)
                on conflict (sha256) do nothing
            """),
            {
                "sha256": sub_sha,
                "size_bytes": len(sub_bytes),
                "storage_key": sub_key,
                "original_filename": file_sub.filename,
                "mime_type": file_sub.content_type,
            },
        )

        # Next rev number
        next_rev = db.execute(
            text("""
                select coalesce(max(rev_number), 0) + 1 as next_rev
                from nc_program_revisions
                where program_id = :program_id
            """),
            {"program_id": program_id},
        ).fetchone()
        rev_number = int(next_rev.next_rev) if next_rev else 1

        revision_row = db.execute(
            text("""
                insert into nc_program_revisions (program_id, rev_number, note, created_by_employee_id)
                values (:program_id, :rev_number, :note, :created_by_employee_id)
                returning id
            """),
            {
                "program_id": program_id,
                "rev_number": rev_number,
                "note": note,
                "created_by_employee_id": created_by_employee_id,
            },
        ).fetchone()

        if not revision_row or not getattr(revision_row, "id", None):
            raise Exception("Не удалось создать ревизию")

        revision_id = int(revision_row.id)

        main_file_id = db.execute(
            text("select id from file_blobs where sha256 = :sha256 limit 1"),
            {"sha256": main_sha},
        ).fetchone()
        sub_file_id = db.execute(
            text("select id from file_blobs where sha256 = :sha256 limit 1"),
            {"sha256": sub_sha},
        ).fetchone()
        if not main_file_id or not sub_file_id:
            raise Exception("Не удалось получить file_id по sha256")

        db.execute(
            text("""
                insert into nc_program_revision_files (revision_id, file_id, role)
                values (:revision_id, :file_id, :role)
            """),
            {"revision_id": revision_id, "file_id": int(main_file_id.id), "role": "ch1"},
        )
        db.execute(
            text("""
                insert into nc_program_revision_files (revision_id, file_id, role)
                values (:revision_id, :file_id, :role)
            """),
            {"revision_id": revision_id, "file_id": int(sub_file_id.id), "role": "ch2"},
        )

        db.commit()

        return {
            "success": True,
            "program_id": program_id,
            "revision_id": revision_id,
            "rev_number": rev_number,
            "files": {"ch1_sha256": main_sha, "ch2_sha256": sub_sha},
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки ревизии: {e}")


@router.post("/programs/{program_id}/revisions/multi", summary="Загрузить новую ревизию (multi-channel: ch1/ch2/ch3/nc)")
async def upload_revision_multi(
    program_id: int,
    channel_keys_json: str = Form(..., description="JSON array of channel keys, aligned with uploaded files order"),
    files: List[UploadFile] = File(...),
    note: Optional[str] = Form(None),
    created_by_employee_id: Optional[int] = Form(None),
    db: Session = Depends(get_db_session),
):
    """
    Multi-channel upload endpoint.

    Example FormData:
    - channel_keys_json = '["ch1","ch2"]'
    - files = [<file ch1>, <file ch2>]
    """
    prog = _ensure_program_exists(db, program_id)

    try:
        raw = json.loads(channel_keys_json or "[]")
        if not isinstance(raw, list):
            raise Exception("channel_keys_json must be a JSON array")
        channel_keys = [_normalize_channel_key(str(x)) for x in raw]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Некорректный channel_keys_json: {e}")

    if not channel_keys or not files:
        raise HTTPException(status_code=400, detail="Нужно передать channel_keys_json и files")
    if len(channel_keys) != len(files):
        raise HTTPException(status_code=400, detail="Количество channel keys должно совпадать с количеством файлов")

    profile = _load_machine_type_profile(db, prog.get("machine_type") or "")
    required = set(_required_channel_keys(profile))
    provided = set(channel_keys)
    if required != provided:
        raise HTTPException(
            status_code=400,
            detail=f"Набор каналов не совпадает с профилем machine_type. required={sorted(required)} provided={sorted(provided)}",
        )

    # Read bytes + validate non-empty
    file_bytes_by_channel: Dict[str, bytes] = {}
    filenames_by_channel: Dict[str, str] = {}
    mime_by_channel: Dict[str, Optional[str]] = {}
    for ch, f in zip(channel_keys, files):
        if not f.filename:
            raise HTTPException(status_code=400, detail=f"Пустое имя файла для {ch}")
        b = await f.read()
        if not b:
            raise HTTPException(status_code=400, detail=f"Пустой файл для {ch}")
        file_bytes_by_channel[ch] = b
        filenames_by_channel[ch] = f.filename
        mime_by_channel[ch] = f.content_type

    # Persist blobs + create revision
    try:
        # Ensure file_blobs rows exist + save to volume
        sha_by_channel: Dict[str, str] = {}
        file_id_by_channel: Dict[str, int] = {}

        for ch, b in file_bytes_by_channel.items():
            sha = _sha256_hex(b)
            sha_by_channel[ch] = sha
            key = f"blobs/{sha}"
            path = PROGRAMS_DIR / key
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b)

            db.execute(
                text("""
                    insert into file_blobs (sha256, size_bytes, storage_key, original_filename, mime_type)
                    values (:sha256, :size_bytes, :storage_key, :original_filename, :mime_type)
                    on conflict (sha256) do nothing
                """),
                {
                    "sha256": sha,
                    "size_bytes": len(b),
                    "storage_key": key,
                    "original_filename": filenames_by_channel.get(ch),
                    "mime_type": mime_by_channel.get(ch),
                },
            )

        # Next rev number
        next_rev = db.execute(
            text("""
                select coalesce(max(rev_number), 0) + 1 as next_rev
                from nc_program_revisions
                where program_id = :program_id
            """),
            {"program_id": program_id},
        ).fetchone()
        rev_number = int(next_rev.next_rev) if next_rev else 1

        revision_row = db.execute(
            text("""
                insert into nc_program_revisions (program_id, rev_number, note, created_by_employee_id)
                values (:program_id, :rev_number, :note, :created_by_employee_id)
                returning id
            """),
            {
                "program_id": program_id,
                "rev_number": rev_number,
                "note": note,
                "created_by_employee_id": created_by_employee_id,
            },
        ).fetchone()
        if not revision_row or not getattr(revision_row, "id", None):
            raise Exception("Не удалось создать ревизию")
        revision_id = int(revision_row.id)

        # Resolve file IDs + insert links
        for ch, sha in sha_by_channel.items():
            row = db.execute(
                text("select id from file_blobs where sha256 = :sha256 limit 1"),
                {"sha256": sha},
            ).fetchone()
            if not row:
                raise Exception(f"Не удалось получить file_id по sha256 для {ch}")
            file_id_by_channel[ch] = int(row.id)

        for ch, fid in file_id_by_channel.items():
            db.execute(
                text("""
                    insert into nc_program_revision_files (revision_id, file_id, role)
                    values (:revision_id, :file_id, :role)
                """),
                {"revision_id": revision_id, "file_id": fid, "role": ch},
            )

        db.commit()
        return {
            "success": True,
            "program_id": program_id,
            "revision_id": revision_id,
            "rev_number": rev_number,
            "files": {ch: sha for ch, sha in sha_by_channel.items()},
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки ревизии: {e}")


@router.post("/programs/{program_id}/revisions/text", summary="Сохранить новую ревизию из текста (browser editor)")
def upload_revision_from_text(
    program_id: int,
    payload: RevisionTextPayload = Body(...),
    db: Session = Depends(get_db_session),
):
    """
    Create a new revision by providing channel texts (UTF-8).
    """
    prog = _ensure_program_exists(db, program_id)
    profile = _load_machine_type_profile(db, prog.get("machine_type") or "")
    required = _required_channel_keys(profile)

    # Normalize and validate channel set
    channels_norm: Dict[str, str] = {}
    for k, v in (payload.channels or {}).items():
        ck = _normalize_channel_key(k)
        if ck:
            channels_norm[ck] = v or ""
    provided = sorted(channels_norm.keys())
    if sorted(required) != provided:
        raise HTTPException(
            status_code=400,
            detail=f"Набор каналов должен совпадать с профилем machine_type. required={sorted(required)} provided={provided}",
        )

    try:
        # Next rev number
        next_rev = db.execute(
            text("""
                select coalesce(max(rev_number), 0) + 1 as next_rev
                from nc_program_revisions
                where program_id = :program_id
            """),
            {"program_id": program_id},
        ).fetchone()
        rev_number = int(next_rev.next_rev) if next_rev else 1

        revision_row = db.execute(
            text("""
                insert into nc_program_revisions (program_id, rev_number, note, created_by_employee_id)
                values (:program_id, :rev_number, :note, :created_by_employee_id)
                returning id
            """),
            {
                "program_id": program_id,
                "rev_number": rev_number,
                "note": payload.note,
                "created_by_employee_id": payload.created_by_employee_id,
            },
        ).fetchone()
        if not revision_row or not getattr(revision_row, "id", None):
            raise Exception("Не удалось создать ревизию")
        revision_id = int(revision_row.id)

        sha_by_channel: Dict[str, str] = {}
        for ch, text_value in channels_norm.items():
            b = (text_value or "").encode("utf-8", errors="replace")
            if not b:
                raise HTTPException(status_code=400, detail=f"Пустой текст для {ch}")
            sha = _sha256_hex(b)
            sha_by_channel[ch] = sha
            key = f"blobs/{sha}"
            path = PROGRAMS_DIR / key
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b)

            filename = f"{prog.get('machine_type') or 'machine'}__program{program_id}__rev{rev_number}__{ch}.nc"
            db.execute(
                text("""
                    insert into file_blobs (sha256, size_bytes, storage_key, original_filename, mime_type)
                    values (:sha256, :size_bytes, :storage_key, :original_filename, :mime_type)
                    on conflict (sha256) do nothing
                """),
                {
                    "sha256": sha,
                    "size_bytes": len(b),
                    "storage_key": key,
                    "original_filename": filename,
                    "mime_type": "text/plain",
                },
            )

        for ch, sha in sha_by_channel.items():
            row = db.execute(
                text("select id from file_blobs where sha256 = :sha256 limit 1"),
                {"sha256": sha},
            ).fetchone()
            if not row:
                raise Exception(f"Не удалось получить file_id по sha256 для {ch}")
            db.execute(
                text("""
                    insert into nc_program_revision_files (revision_id, file_id, role)
                    values (:revision_id, :file_id, :role)
                """),
                {"revision_id": revision_id, "file_id": int(row.id), "role": ch},
            )

        db.commit()
        return {
            "success": True,
            "program_id": program_id,
            "revision_id": revision_id,
            "rev_number": rev_number,
            "files": {ch: sha for ch, sha in sha_by_channel.items()},
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка сохранения ревизии: {e}")

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки ревизии: {e}")


@router.get("/programs/{program_id}/revisions", summary="Список ревизий программы (история)")
def list_program_revisions(
    program_id: int,
    db: Session = Depends(get_db_session),
):
    """
    Returns all revisions for a program with their files (channels) and sha256.
    """
    _ensure_program_exists(db, program_id)

    rows = db.execute(
        text("""
            select
                r.id as revision_id,
                r.rev_number,
                r.note,
                r.created_at,
                rf.role as file_role,
                fb.id as file_id,
                fb.sha256,
                fb.size_bytes,
                fb.original_filename
            from nc_program_revisions r
            left join nc_program_revision_files rf on rf.revision_id = r.id
            left join file_blobs fb on fb.id = rf.file_id
            where r.program_id = :program_id
            order by r.rev_number asc, rf.role asc
        """),
        {"program_id": program_id},
    ).mappings().all()

    prog = _ensure_program_exists(db, program_id)
    profile = _load_machine_type_profile(db, prog.get("machine_type") or "")
    required_keys = _required_channel_keys(profile)

    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        rid = int(row["revision_id"])
        rev = out.get(rid)
        if not rev:
            rev = {
                "revision_id": rid,
                "rev_number": int(row["rev_number"]),
                "note": row["note"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "files": {k: None for k in required_keys},
            }
            out[rid] = rev

        if row["file_role"] and row["file_id"] and row["sha256"]:
            channel_key = _normalize_channel_key(row["file_role"])
            if channel_key and channel_key not in rev["files"]:
                rev["files"][channel_key] = None
            if channel_key:
                rev["files"][channel_key] = {
                    "file_id": int(row["file_id"]),
                    "sha256": row["sha256"],
                    "size_bytes": int(row["size_bytes"]) if row["size_bytes"] is not None else None,
                    "original_filename": row["original_filename"],
                }

    return {
        "program_id": program_id,
        "machine_type": prog.get("machine_type"),
        "channels_profile": profile,
        "revisions": list(out.values()),
    }


@router.get("/revisions/{revision_id}/files/{role}", summary="Скачать файл ревизии (legacy: main/sub)")
def download_revision_file(
    revision_id: int,
    role: str,
    db: Session = Depends(get_db_session),
):
    role = (role or "").strip().lower()
    if role not in ("main", "sub"):
        raise HTTPException(status_code=400, detail="role должен быть main или sub")

    candidates = _candidates_for_role_or_channel(role)

    row = db.execute(
        text("""
            select
                fb.storage_key,
                fb.original_filename
            from nc_program_revision_files rf
            join file_blobs fb on fb.id = rf.file_id
            where rf.revision_id = :revision_id
              and rf.role = any(:roles)
            limit 1
        """),
        {"revision_id": revision_id, "roles": candidates},
    ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Файл не найден")

    storage_key = row.storage_key
    filename = row.original_filename or f"revision-{revision_id}-{role}.nc"
    file_path = PROGRAMS_DIR / storage_key

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Файл отсутствует в хранилище")

    return FileResponse(
        path=file_path,
        media_type="application/octet-stream",
        filename=filename,
    )


@router.get("/revisions/{revision_id}/channels/{channel_key}", summary="Скачать файл ревизии (channel_key)")
def download_revision_channel(
    revision_id: int,
    channel_key: str,
    db: Session = Depends(get_db_session),
):
    ch = _normalize_channel_key(channel_key)
    if not ch:
        raise HTTPException(status_code=400, detail="channel_key обязателен")

    candidates = _candidates_for_role_or_channel(ch)
    row = db.execute(
        text("""
            select
                fb.storage_key,
                fb.original_filename
            from nc_program_revision_files rf
            join file_blobs fb on fb.id = rf.file_id
            where rf.revision_id = :revision_id
              and rf.role = any(:roles)
            limit 1
        """),
        {"revision_id": revision_id, "roles": candidates},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Файл не найден")

    storage_key = row.storage_key
    filename = row.original_filename or f"revision-{revision_id}-{ch}.nc"
    file_path = PROGRAMS_DIR / storage_key
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Файл отсутствует в хранилище")

    return FileResponse(path=file_path, media_type="application/octet-stream", filename=filename)


@router.get("/revisions/{revision_id}/channels/{channel_key}/text", summary="Текстовый просмотр канала ревизии (preview)")
def preview_revision_channel_text(
    revision_id: int,
    channel_key: str,
    limit_bytes: int = 250_000,
    db: Session = Depends(get_db_session),
):
    """
    Returns plain text for browser preview. Files are treated as UTF-8/ASCII.
    limit_bytes protects against huge files.
    """
    ch = _normalize_channel_key(channel_key)
    if not ch:
        raise HTTPException(status_code=400, detail="channel_key обязателен")
    if limit_bytes < 1:
        limit_bytes = 1
    if limit_bytes > 2_000_000:
        limit_bytes = 2_000_000

    candidates = _candidates_for_role_or_channel(ch)
    row = db.execute(
        text("""
            select fb.storage_key
            from nc_program_revision_files rf
            join file_blobs fb on fb.id = rf.file_id
            where rf.revision_id = :revision_id
              and rf.role = any(:roles)
            limit 1
        """),
        {"revision_id": revision_id, "roles": candidates},
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Файл не найден")

    file_path = PROGRAMS_DIR / row.storage_key
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Файл отсутствует в хранилище")

    with open(file_path, "rb") as f:
        data = f.read(limit_bytes)
    text_value = data.decode("utf-8", errors="replace")
    return PlainTextResponse(text_value, media_type="text/plain; charset=utf-8")

