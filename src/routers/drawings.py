"""
API для работы с чертежами (PDF файлы)
Хранятся в Railway Volume: /app/drawings
"""
import os
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, UploadFile, File, HTTPException, Response
from fastapi.responses import FileResponse
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/drawings", tags=["Drawings"])

# Путь к Volume
DRAWINGS_DIR = Path("/app/drawings")

# Создать папку если не существует
DRAWINGS_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/upload")
async def upload_drawing(
    file: UploadFile = File(...),
    drawing_number: Optional[str] = None
):
    """
    Загрузить чертеж (PDF файл)
    
    - file: PDF файл
    - drawing_number: номер чертежа (если не указан, берется из имени файла)
    """
    try:
        # Определить номер чертежа
        if not drawing_number:
            # Извлечь из имени файла (например "1000-03.pdf" -> "1000-03")
            drawing_number = Path(file.filename).stem
        
        # Валидация
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="Файл должен быть PDF")
        
        # Путь для сохранения
        file_path = DRAWINGS_DIR / f"{drawing_number}.pdf"
        
        # Сохранить файл
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)
        
        logger.info(f"✅ Чертеж загружен: {drawing_number}.pdf ({len(content)} bytes)")
        
        # URL для доступа к файлу
        file_url = f"/drawings/{drawing_number}"
        
        return {
            "success": True,
            "drawing_number": drawing_number,
            "file_url": file_url,
            "file_size": len(content)
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки чертежа: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{drawing_number}")
async def get_drawing(drawing_number: str):
    """
    Получить чертеж по номеру
    
    - drawing_number: номер чертежа (например "1000-03")
    
    Возвращает PDF файл для просмотра в браузере
    """
    try:
        # Убрать .pdf если есть
        if drawing_number.endswith('.pdf'):
            drawing_number = drawing_number[:-4]
        
        file_path = DRAWINGS_DIR / f"{drawing_number}.pdf"
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"Чертеж {drawing_number} не найден")
        
        # Вернуть файл с правильным content-type для просмотра в браузере
        return FileResponse(
            path=file_path,
            media_type="application/pdf",
            filename=f"{drawing_number}.pdf",
            headers={
                "Content-Disposition": f"inline; filename={drawing_number}.pdf"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка получения чертежа: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/exists/{drawing_number}")
async def check_drawing_exists(drawing_number: str):
    """
    Проверить существование чертежа
    
    - drawing_number: номер чертежа (например "1000-03")
    """
    if drawing_number.endswith('.pdf'):
        drawing_number = drawing_number[:-4]
    
    file_path = DRAWINGS_DIR / f"{drawing_number}.pdf"
    
    return {
        "exists": file_path.exists(),
        "drawing_number": drawing_number,
        "file_path": str(file_path) if file_path.exists() else None
    }


@router.delete("/{drawing_number}")
async def delete_drawing(drawing_number: str):
    """
    Удалить чертеж
    
    - drawing_number: номер чертежа (например "1000-03")
    """
    try:
        if drawing_number.endswith('.pdf'):
            drawing_number = drawing_number[:-4]
        
        file_path = DRAWINGS_DIR / f"{drawing_number}.pdf"
        
        if not file_path.exists():
            raise HTTPException(status_code=404, detail=f"Чертеж {drawing_number} не найден")
        
        file_path.unlink()
        
        logger.info(f"🗑️ Чертеж удален: {drawing_number}.pdf")
        
        return {
            "success": True,
            "drawing_number": drawing_number,
            "message": "Чертеж удален"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ошибка удаления чертежа: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/")
async def list_drawings(limit: int = 100, offset: int = 0):
    """
    Список всех чертежей
    
    - limit: максимум записей
    - offset: сдвиг для пагинации
    """
    try:
        # Получить все PDF файлы
        all_files = sorted(DRAWINGS_DIR.glob("*.pdf"))
        
        # Пагинация
        files = all_files[offset:offset + limit]
        
        drawings = []
        for file_path in files:
            drawings.append({
                "drawing_number": file_path.stem,
                "file_name": file_path.name,
                "file_size": file_path.stat().st_size,
                "file_url": f"/drawings/{file_path.stem}"
            })
        
        return {
            "total": len(all_files),
            "limit": limit,
            "offset": offset,
            "drawings": drawings
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения списка чертежей: {e}")
        raise HTTPException(status_code=500, detail=str(e))


