import subprocess
import json
import os
from pathlib import Path
from typing import Dict, Any
from solid_dashboard.interfaces.analyzer import IAnalyzer  # явный импорт интерфейса

lizard = None

try:
    import lizard as _lizard  # type: ignore[import]
    lizard = _lizard
    LIZARD_AVAILABLE = True
except ImportError:
    LIZARD_AVAILABLE = False


class RadonAdapter(IAnalyzer): 
    @property
    def name(self) -> str:
        return "radon"

    def run(self, target_dir: str, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Запускает утилиту radon для вычисления цикломатической сложности (Cyclomatic Complexity).
        Возвращает агрегированную статистику и список всех проанализированных функций/классов.
        """
        # Запускаем radon как системный процесс с выводом в формате JSON
        # Флаг 'cc' означает cyclomatic complexity, '-a' - включает среднее значение, '-s' - вывод сложности
        cmd = ["radon", "cc", "--json", target_dir]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            raw_data = json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            return {"error": f"Radon execution failed: {e.stderr}"}
        except json.JSONDecodeError:
            return {"error": "Failed to parse Radon JSON output"}

        items = []
        high_complexity_count = 0
        total_cc = 0

        # Парсим сырой JSON от radon. Он возвращает словарь, где ключи - пути к файлам.
        for filepath, blocks in raw_data.items():
            # Radon иногда возвращает строку "error" для файлов, которые не смог прочитать
            if isinstance(blocks, str):
                continue
                
            for block in blocks:
                # Нас интересуют только функции (F) и методы (M)
                if block.get("type") in ["function", "method"]:
                    complexity = block.get("complexity", 0)
                    total_cc += complexity
                    
                    if complexity > 10:  # Порог CC > 10 считается высоким риском
                        high_complexity_count += 1
                        
                    items.append({
                        "name": block.get("name"),
                        "type": block.get("type"),
                        "complexity": complexity,
                        "rank": block.get("rank", "A"),
                        "lineno": block.get("lineno", 0),
                        "filepath": filepath
                    })

        # Интеграция LIZARD (Опциональное расширение)
        if lizard is not None and items:
            # 1. Анализируем ту же директорию через Lizard
            lizard_results = lizard.analyze([target_dir])
            
            # 2. Строим индекс для быстрого поиска: { 'абсолютный_путь': { номер_строки: функция } }
            lizard_index = {}
            for file_info in lizard_results:
                try:
                    # Используем Path.resolve() для 100% точного сопоставления путей Radon и Lizard
                    abs_path = str(Path(file_info.filename).resolve())
                    if abs_path not in lizard_index:
                        lizard_index[abs_path] = {}
                    
                    for func in file_info.function_list:
                        lizard_index[abs_path][func.start_line] = func
                except Exception:
                    continue  # Игнорируем файлы, где не удалось прочитать путь
            
            # 3. Обогащаем метрики Radon данными из Lizard
            for item in items:
                filepath = item.get("filepath")
                lineno = item.get("lineno")
                
                if filepath and lineno:
                    try:
                        abs_path = str(Path(filepath).resolve())
                        # Ищем функцию в индексе Lizard по пути и строке начала
                        liz_func = lizard_index.get(abs_path, {}).get(lineno)
                        
                        if liz_func:
                            # Добавляем ТОЛЬКО уникальные метрики
                            item["parameter_count"] = liz_func.parameter_count
                    except Exception:
                        pass  # В случае любой ошибки поиска просто пропускаем этот item
        
        total_items = len(items)
        mean_cc = round(total_cc / total_items, 2) if total_items > 0 else 0.0

        return {
            "total_items": total_items,
            "mean_cc": mean_cc,
            "high_complexity_count": high_complexity_count,
            "items": sorted(items, key=lambda x: x["complexity"], reverse=True), # Сортируем от самых сложных к простым
            "lizard_used": LIZARD_AVAILABLE # Флаг для отладки, показывает сработал ли Lizard
        }