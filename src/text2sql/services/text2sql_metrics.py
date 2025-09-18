"""
Метрики качества для Text2SQL
- EX (Exact Match): точное совпадение SQL запросов
- Soft Accuracy: семантическое совпадение результатов
"""

import re
from typing import Dict, List, Tuple, Any
from sqlalchemy.orm import Session
from sqlalchemy import text


class Text2SQLMetrics:
    def __init__(self, db: Session):
        self.db = db
    
    def normalize_sql(self, sql: str) -> str:
        """Нормализация SQL для сравнения"""
        # Убираем лишние пробелы и переводы строк
        sql = re.sub(r'\s+', ' ', sql.strip())
        # Приводим к нижнему регистру
        sql = sql.lower()
        # Убираем точки с запятой в конце
        sql = sql.rstrip(';')
        return sql
    
    def exact_match(self, predicted_sql: str, ground_truth_sql: str) -> bool:
        """EX метрика: точное совпадение SQL"""
        pred_norm = self.normalize_sql(predicted_sql)
        gt_norm = self.normalize_sql(ground_truth_sql)
        return pred_norm == gt_norm
    
    def execute_sql_safe(self, sql: str) -> Tuple[bool, List[Dict[str, Any]]]:
        """Безопасное выполнение SQL с обработкой ошибок"""
        try:
            with self.db.begin():
                result = self.db.execute(text(sql))
                cols = list(result.keys())
                rows = [dict(zip(cols, r)) for r in result.fetchall()]
                return True, rows
        except Exception as e:
            print(f"SQL execution error: {e}")
            return False, []
    
    def soft_accuracy(self, predicted_sql: str, ground_truth_sql: str) -> float:
        """Soft Accuracy: сравнение результатов выполнения SQL"""
        # Выполняем оба запроса
        pred_success, pred_rows = self.execute_sql_safe(predicted_sql)
        gt_success, gt_rows = self.execute_sql_safe(ground_truth_sql)
        
        # Если один из запросов не выполнился
        if not pred_success or not gt_success:
            return 0.0
        
        # Если количество строк разное
        if len(pred_rows) != len(gt_rows):
            return 0.0
        
        # Если нет строк
        if len(pred_rows) == 0:
            return 1.0 if len(gt_rows) == 0 else 0.0
        
        # Сравниваем результаты
        total_cells = 0
        matching_cells = 0
        
        for pred_row, gt_row in zip(pred_rows, gt_rows):
            for key in pred_row:
                if key in gt_row:
                    total_cells += 1
                    if pred_row[key] == gt_row[key]:
                        matching_cells += 1
        
        return matching_cells / total_cells if total_cells > 0 else 0.0
    
    def evaluate_batch(self, test_cases: List[Dict[str, str]]) -> Dict[str, float]:
        """Оценка набора тестовых случаев"""
        ex_scores = []
        soft_scores = []
        
        for case in test_cases:
            question = case['question']
            ground_truth = case['ground_truth']
            predicted = case['predicted']
            
            # EX метрика
            ex_score = 1.0 if self.exact_match(predicted, ground_truth) else 0.0
            ex_scores.append(ex_score)
            
            # Soft Accuracy
            soft_score = self.soft_accuracy(predicted, ground_truth)
            soft_scores.append(soft_score)
        
        return {
            'exact_match': sum(ex_scores) / len(ex_scores) if ex_scores else 0.0,
            'soft_accuracy': sum(soft_scores) / len(soft_scores) if soft_scores else 0.0,
            'total_cases': len(test_cases)
        }
    
    def create_test_cases(self) -> List[Dict[str, str]]:
        """Создание тестовых случаев на основе few-shot примеров"""
        return [
            {
                'question': 'сколько всего записей в таблице batches?',
                'ground_truth': 'SELECT COUNT(*) as total_batches FROM batches;',
                'predicted': 'SELECT COUNT(*) as count FROM batches'  # Будет заменено реальным предсказанием
            },
            {
                'question': 'сколько открытых батчей?',
                'ground_truth': 'SELECT COUNT(*) as open_batches FROM batches WHERE status = \'open\';',
                'predicted': 'SELECT COUNT(*) as open_batches FROM batches WHERE status = \'open\''  # Будет заменено
            },
            {
                'question': 'какое сейчас время?',
                'ground_truth': 'SELECT NOW() as current_time;',
                'predicted': 'SELECT NOW() as current_time'  # Будет заменено
            },
            {
                'question': 'покажи все станки',
                'ground_truth': 'SELECT machine_id, machine_name, area_name FROM machines;',
                'predicted': 'SELECT machine_id, machine_name, area_name FROM machines'  # Будет заменено
            },
            {
                'question': 'покажи количество батчей по статусам',
                'ground_truth': 'SELECT status, COUNT(*) as count FROM batches GROUP BY status;',
                'predicted': 'SELECT status, COUNT(*) as count FROM batches GROUP BY status'  # Будет заменено
            }
        ]
