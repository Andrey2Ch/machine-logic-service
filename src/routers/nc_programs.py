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
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..database import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/nc-programs", tags=["NC Programs"])

PROGRAMS_DIR = Path("/app/programs")
BLOBS_DIR = PROGRAMS_DIR / "blobs"
BLOBS_DIR.mkdir(parents=True, exist_ok=True)


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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

    # Assemble into { program -> current_revision -> files{main,sub} }
    out: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        pid = int(row["program_id"])
        prog = out.get(pid)
        if not prog:
            prog = {
                "program_id": pid,
                "part_id": row["part_id"],
                "machine_type": row["machine_type"],
                "program_kind": row["program_kind"],
                "title": row["title"],
                "comment": row["comment"],
                "current_revision": None,
            }
            out[pid] = prog

        if row["revision_id"] is None:
            continue

        if prog["current_revision"] is None:
            prog["current_revision"] = {
                "revision_id": int(row["revision_id"]),
                "rev_number": int(row["rev_number"]),
                "note": row["revision_note"],
                "created_at": row["revision_created_at"].isoformat() if row["revision_created_at"] else None,
                "files": {"main": None, "sub": None},
            }

        if row["file_role"] and row["file_id"]:
            role = row["file_role"]
            if role in ("main", "sub"):
                prog["current_revision"]["files"][role] = {
                    "file_id": int(row["file_id"]),
                    "sha256": row["sha256"],
                    "size_bytes": int(row["size_bytes"]) if row["size_bytes"] is not None else None,
                    "original_filename": row["original_filename"],
                }

    return {"part_id": part_id, "programs": list(out.values())}


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
    Creates new revision with two files (main, sub).
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
            {"revision_id": revision_id, "file_id": int(main_file_id.id), "role": "main"},
        )
        db.execute(
            text("""
                insert into nc_program_revision_files (revision_id, file_id, role)
                values (:revision_id, :file_id, :role)
            """),
            {"revision_id": revision_id, "file_id": int(sub_file_id.id), "role": "sub"},
        )

        db.commit()

        return {
            "success": True,
            "program_id": program_id,
            "revision_id": revision_id,
            "rev_number": rev_number,
            "files": {"main_sha256": main_sha, "sub_sha256": sub_sha},
        }
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
    Returns all revisions for a program with their files (main/sub) and sha256.
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
                "files": {"main": None, "sub": None},
            }
            out[rid] = rev

        if row["file_role"] and row["file_id"] and row["sha256"]:
            role = row["file_role"]
            if role in ("main", "sub"):
                rev["files"][role] = {
                    "file_id": int(row["file_id"]),
                    "sha256": row["sha256"],
                    "size_bytes": int(row["size_bytes"]) if row["size_bytes"] is not None else None,
                    "original_filename": row["original_filename"],
                }

    return {"program_id": program_id, "revisions": list(out.values())}


@router.get("/revisions/{revision_id}/files/{role}", summary="Скачать файл ревизии (main/sub)")
def download_revision_file(
    revision_id: int,
    role: str,
    db: Session = Depends(get_db_session),
):
    role = (role or "").strip().lower()
    if role not in ("main", "sub"):
        raise HTTPException(status_code=400, detail="role должен быть main или sub")

    row = db.execute(
        text("""
            select
                fb.storage_key,
                fb.original_filename
            from nc_program_revision_files rf
            join file_blobs fb on fb.id = rf.file_id
            where rf.revision_id = :revision_id
              and rf.role = :role
            limit 1
        """),
        {"revision_id": revision_id, "role": role},
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

