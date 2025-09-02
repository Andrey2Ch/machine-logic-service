from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text
import logging

from src.database import get_db_session

router = APIRouter(prefix="/cards", tags=["Cards"])
logger = logging.getLogger(__name__)


class AssignPayload(BaseModel):
    machine_id: int
    batch_id: int


@router.get("/free")
def get_free_cards(machine_id: int, limit: int = 10, db: Session = Depends(get_db_session)):
    logger.info(f"/cards/free mid={machine_id} limit={limit}")
    rows = db.execute(text(
        """
        select card_number
        from public.cards
        where machine_id = :mid
          and batch_id is null
        order by card_number
        limit :lim
        """
    ), {"mid": machine_id, "lim": limit}).fetchall()
    cards = [r.card_number for r in rows]
    logger.info(f"/cards/free mid={machine_id} -> {cards}")
    return {"cards": cards}


@router.get("/used")
def get_cards_state(machine_id: int, db: Session = Depends(get_db_session)):
    """Возвращает полное состояние карточек для станка (card_number, status, batch_id).

    Семантика упрощена: фронт/бот определяют занятость по status, а не по batch_id.
    """
    logger.info(f"/cards/used (state) mid={machine_id}")
    rows = db.execute(text(
        """
        select card_number, status, batch_id
        from public.cards
        where machine_id = :mid
        order by card_number
        """
    ), {"mid": machine_id}).fetchall()
    cards = [{"card_number": r.card_number, "status": r.status, "batch_id": r.batch_id} for r in rows]
    logger.info(f"/cards/used mid={machine_id} -> {cards}")
    return {"cards": cards}


@router.patch("/{card_number}/use")
def use_card(card_number: int, machine_id: int, batch_id: int, db: Session = Depends(get_db_session)):
    logger.info(f"PATCH /cards/{card_number}/use mid={machine_id} bid={batch_id}")
    cur = db.execute(text(
        """
        select status, batch_id
        from public.cards
        where machine_id = :mid and card_number = :num
        for update
        """
    ), {"mid": machine_id, "num": card_number}).first()
    if not cur:
        raise HTTPException(status_code=404, detail="Card not found")

    logger.info(f"current status={cur.status}, batch_id={cur.batch_id}")
    if cur.status == 'in_use' and cur.batch_id == batch_id:
        return {"card_number": card_number}

    updated = db.execute(text(
        """
        update public.cards
        set status = 'in_use', batch_id = :bid, last_event = now()
        where machine_id = :mid and card_number = :num and status = 'free' and batch_id is null
        returning card_number
        """
    ), {"mid": machine_id, "num": card_number, "bid": batch_id}).first()

    if not updated:
        raise HTTPException(status_code=409, detail="Карточка уже занята")
    return {"card_number": card_number}


@router.post("/assign")
def assign_card(payload: AssignPayload, db: Session = Depends(get_db_session)):
    logger.info(f"POST /cards/assign mid={payload.machine_id} bid={payload.batch_id}")
    row = db.execute(text(
        """
        with picked as (
          select card_number
          from public.cards
          where machine_id = :mid and status = 'free' and batch_id is null
          order by card_number
          for update skip locked
          limit 1
        )
        update public.cards c
        set status = 'in_use', batch_id = :bid, last_event = now()
        from picked
        where c.machine_id = :mid and c.card_number = picked.card_number
        returning c.card_number
        """
    ), {"mid": payload.machine_id, "bid": payload.batch_id}).first()
    if not row:
        raise HTTPException(status_code=409, detail="Нет свободных карточек")
    return {"card_number": row.card_number}


@router.get("/debug")
def debug_cards(machine_id: int, db: Session = Depends(get_db_session)):
    rows = db.execute(text(
        """
        select card_number, status, batch_id
        from public.cards
        where machine_id = :mid
        order by card_number
        """
    ), {"mid": machine_id}).fetchall()
    cards = [{"card_number": r.card_number, "status": r.status, "batch_id": r.batch_id} for r in rows]
    logger.info(f"/cards/debug mid={machine_id} -> {cards}")
    return {"cards": cards}


