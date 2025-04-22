import gspread
from google.oauth2.service_account import Credentials
import logging
from datetime import datetime
import pytz

logger = logging.getLogger(__name__)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def init_google_sheets():
    try:
        credentials = Credentials.from_service_account_file(
            'civil-hull-441511-n5-c1b57e3e67d1.json',
            scopes=SCOPES
        )
        
        gc = gspread.authorize(credentials)
        SPREADSHEET_ID = '1aHjKbs4v6wgdqDpHP7_JWxY8pbVNW5betIXlt1GK2y8'
        worksheet = gc.open_by_key(SPREADSHEET_ID).sheet1
        
        logger.info("Successfully connected to Google Sheets")
        return worksheet
    except Exception as e:
        logger.error(f"Error connecting to Google Sheets: {e}")
        return None

async def normalize_machine_name(machine: str) -> str:
    """Нормализует название станка для соответствия таблице"""
    machine_mappings = {
        'XD-38': 'XD38',
        'XD-20': 'XD20',
        'K-16-3': 'K16-3',
        'K-16-2': 'K16-2',
        'K-16': 'K16',
        'SR-32': 'SR32',
        'SR-24': 'SR24',
        'SR-25': 'SR25',
        'SR-23': 'SR23',
        'SR-26': 'SR26',
        'SR-21': 'SR21',
        'SR-20': 'SR20',
        'SR-22': 'SR22',
        'SR-10': 'SR10',
        'SB-16': 'SB16',
        'D-26': 'D26',
        'L-20': 'L20',
        'B-38': 'B38',
    }
    return machine_mappings.get(machine, machine)

async def normalize_operator_name(operator: str) -> str:
    """Нормализует имя оператора для соответствия таблице"""
    operator_mappings = {
        'Roman': 'Roman',
        'Misha V': 'Misha V',
        'Misha Z': 'Misha Z',
        'Sergey K': 'Sergey K',
        'Vova': 'Vova',
        'Alex': 'Alex',
        'Sergey M': 'Sergey M',
        'Sergey Z': 'Sergey Z',
        'Kavanim': 'Kavanim'
    }
    return operator_mappings.get(operator, operator)

async def save_to_sheets(operator: str, machine: str, reading: int):
    logger.info(f"Attempting to save to sheets: operator={operator}, machine={machine}, reading={reading}")
    
    worksheet = init_google_sheets()
    if not worksheet:
        logger.error("Failed to initialize Google Sheets")
        return False
        
    try:
        # Получаем все значения для поиска индексов
        logger.info("Getting all values from sheet")
        all_values = worksheet.get_all_values()
        
        # Нормализуем название станка и имя оператора
        normalized_machine = await normalize_machine_name(machine)
        normalized_operator = await normalize_operator_name(operator)
        logger.info(f"Normalized machine name: {machine} -> {normalized_machine}")
        logger.info(f"Normalized operator name: {operator} -> {normalized_operator}")
        
        # Находим индекс строки для станка
        machine_row = None
        for i, row in enumerate(all_values):
            if row[0].strip() == normalized_machine:
                machine_row = i + 1
                break
                
        if not machine_row:
            logger.error(f"Machine {machine} (normalized: {normalized_machine}) not found in sheet")
            return False
            
        logger.info(f"Found machine row: {machine_row}")
            
        # Находим индекс столбца для оператора
        operator_col = None
        headers = all_values[0]
        for i, header in enumerate(headers):
            if header.strip() == normalized_operator:
                operator_col = i + 1
                break
                
        if not operator_col:
            logger.error(f"Operator {normalized_operator} not found in sheet. Available headers: {headers}")
            return False
            
        logger.info(f"Found operator column: {operator_col}")
            
        # Обновляем конкретную ячейку
        logger.info(f"Updating cell at row={machine_row}, col={operator_col} with value={reading}")
        worksheet.update_cell(machine_row, operator_col, str(reading))
        
        logger.info(f"Successfully saved to sheet: machine={machine}({machine_row}), operator={operator}({operator_col}), reading={reading}")
        return True
        
    except Exception as e:
        logger.error(f"Error saving to Google Sheets: {e}", exc_info=True)
        return False 