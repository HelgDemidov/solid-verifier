import subprocess
import re
from typing import Dict, Any

class CohesionAdapter:
    @property
    def name(self) -> str:
        return "cohesion"

    def run(
        self,
        target_dir: str,
        context: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Запускает утилиту cohesion для оценки принципа единой ответственности (SRP) классов.
        Парсит текстовый вывод и отфильтровывает классы со слишком малым количеством методов.
        """
        # Флаг '-d' указывает директорию для анализа
        cmd = ["cohesion", "-d", target_dir]
        
        try:
            # cohesion часто возвращает не-нулевой код возврата, если находит классы с низкой связностью,
            # поэтому мы не используем check=True, а просто читаем stdout.
            result = subprocess.run(cmd, capture_output=True, text=True)
            output = result.stdout
        except Exception as e:
            return {"error": f"Cohesion execution failed: {str(e)}"}

        classes = []
        low_cohesion_count = 0
        total_cohesion = 0.0
        
        # Регулярное выражение для парсинга вывода cohesion.
        # Пример строки вывода: "app/models/user.py User 33.33 % (2/6)"
        # Группы: 1-файл, 2-имя класса, 3-процент, 4-методов_использовано, 5-всего_методов
        pattern = re.compile(r'^(.*?)\s+([A-Za-z0-9_]+)\s+([\d\.]+)\s+%\s+\((\d+)/(\d+)\)', re.MULTILINE)
        
        for match in pattern.finditer(output):
            filepath = match.group(1).strip()
            class_name = match.group(2)
            score = float(match.group(3))
            total_methods = int(match.group(5))
            
            # Фильтр из ТЗ: игнорируем мелкие классы (например, Pydantic модели),
            # так как метрика связности для них не имеет архитектурного смысла.
            if total_methods < 4:
                continue
                
            classes.append({
                "name": class_name,
                "filepath": filepath,
                "methods_count": total_methods,
                "cohesion_score": score
            })
            
            total_cohesion += score
            if score < 50.0:  # Если связность ниже 50%, класс - кандидат на рефакторинг (нарушение SRP)
                low_cohesion_count += 1

        total_classes = len(classes)
        mean_cohesion = round(total_cohesion / total_classes, 2) if total_classes > 0 else 0.0

        return {
            "total_classes_analyzed": total_classes,
            "mean_cohesion": mean_cohesion,
            "low_cohesion_count": low_cohesion_count,
            "classes": sorted(classes, key=lambda x: x["cohesion_score"]) # Сортируем от худших к лучшим
        }
