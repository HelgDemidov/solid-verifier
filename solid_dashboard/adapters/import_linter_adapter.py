import os  # работа с путями и файлами
import re  # разбор текста и ANSI-кодов
import subprocess  # запуск lint-imports как отдельного процесса
from typing import Any, Dict, List  # типы для аннотаций

from solid_dashboard.interfaces.analyzer import IAnalyzer  # базовый интерфейс адаптера

# Регулярное выражение для очистки вывода от ANSI-кодов (цветной вывод, рамки и т.п.)
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class ImportLinterAdapter(IAnalyzer):
    """
    Адаптер для вызова import-linter через стабильный CLI (lint-imports).
    Вместо полного автогенерирования конфига:
    - читает существующий .importlinter
    - обновляет только блок слоёв в контракте типа 'layers' по данным из solidconfig.json
    - сохраняет результат во временный .importlinter_auto
    - запускает lint-imports --config .importlinter_auto
    """

    @property
    def name(self) -> str:
        # Имя адаптера для JSON-отчёта
        return "import_linter"

    def run(self, target_dir: str, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        # Абсолютный путь до анализируемого пакета (например, .../scopus_search_code/app)
        target_path = os.path.abspath(target_dir)
        # Корень репозитория (папка, где лежит .importlinter, pyproject, tools, app и т.п.)
        project_root = os.path.dirname(target_path)

        # Пути к "боевому" и временному конфигам import-linter
        base_config_path = os.path.join(project_root, ".importlinter")
        temp_config_path = os.path.join(project_root, ".importlinter_auto")

        # Проверяем, что базовый конфиг существует (иначе смысла продолжать нет)
        if not os.path.exists(base_config_path):
            return self._error_message(f".importlinter not found at {base_config_path}")

        try:
            # 1. Читаем базовый .importlinter и обновляем в нём блок слоёв
            self._generate_synced_config(
                base_config_path=base_config_path,
                solid_config=config,
                out_path=temp_config_path,
            )

            # 2. Готовим окружение: добавляем root проекта в PYTHONPATH только для дочернего процесса
            env = os.environ.copy()
            if "PYTHONPATH" in env:
                env["PYTHONPATH"] = f"{project_root}{os.pathsep}{env['PYTHONPATH']}"
            else:
                env["PYTHONPATH"] = project_root

            # 3. Запускаем import-linter через CLI
            cmd = ["lint-imports", "--config", temp_config_path]

            completed = subprocess.run(
                cmd,
                cwd=project_root,   # работаем из корня репо — как ты запускаешь вручную
                env=env,            # проброс корректного PYTHONPATH
                capture_output=True,
                text=True,
                check=False,
            )

            # Склеиваем stdout и stderr и чистим ANSI-коды
            raw_console = (completed.stdout or "") + "\n" + (completed.stderr or "")
            clean_output = ANSI_ESCAPE.sub("", raw_console).strip()

            # Допустимыми считаем только коды 0 (KEPT) и 1 (BROKEN)
            if completed.returncode not in (0, 1):
                return self._error_message(
                    f"lint-imports exited with code {completed.returncode}.\n{clean_output}"
                )

            linting_passed = (completed.returncode == 0)

            # 4. Парсим агрегированные статистики (Contracts X kept, Y broken)
            kept, broken = self._parse_contract_stats(clean_output, linting_passed)

            # 5. Собираем список нарушенных контрактов (по строкам с маркером BROKEN)
            violations: List[str] = []
            for line in clean_output.splitlines():
                stripped = line.strip()
                # Import-linter обычно выводит "Contract Name BROKEN"
                if stripped.endswith("BROKEN"):
                    # Отрезаем суффикс "BROKEN" и лишние пробелы
                    name_part = stripped[: -len("BROKEN")].rstrip()
                    if name_part:
                        violations.append(name_part)

            return {
                "is_success": linting_passed,
                "contracts_checked": kept + broken,
                "broken_contracts": broken,
                "kept_contracts": kept,
                "violations": violations,
                "raw_output": clean_output,
            }

        except FileNotFoundError:
            # Команда lint-imports не найдена в PATH/venv
            return self._error_message(
                "Command 'lint-imports' not found. Ensure import-linter is installed in the active virtual environment."
            )
        except Exception as exc:
            # Любая непредвиденная ошибка адаптера
            return self._error_message(f"ImportLinterAdapter failed: {exc}")
        finally:
            # Аккуратно удаляем временный конфиг, если он был создан
            if os.path.exists(temp_config_path):
                try:
                    os.remove(temp_config_path)
                except OSError:
                    pass

    def _generate_synced_config(
        self,
        base_config_path: str,
        solid_config: Dict[str, Any],
        out_path: str,
    ) -> None:
        """
        Читает существующий .importlinter и обновляет только блок `layers:` в контракте типа `layers`,
        используя список ключей `layers` из solidconfig.json.

        Это устраняет дублирование и сохраняет рабочие root_packages и прочие настройки.
        """
        # Читаем оригинальный конфиг построчно
        with open(base_config_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Получаем список слоёв из solidconfig.json
        layer_config: Dict[str, Any] = solid_config.get("layers", {})
        layer_names = list(layer_config.keys())

        if not layer_names:
            # Если слои не заданы, просто копируем исходный файл без изменений
            with open(out_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return

        result_lines: List[str] = []
        in_layers_contract = False  # находимся ли мы внутри нужного контракта
        in_layers_block = False     # находимся ли мы внутри блока "layers:" конкретного контракта

        for line in lines:
            stripped = line.strip()

            # Находим начало секции контракта типа layers
            if stripped.startswith("[importlinter:contract:") and not in_layers_contract:
                # Пробрасываем сам заголовок секции
                result_lines.append(line)
                in_layers_contract = True
                in_layers_block = False
                continue

            if in_layers_contract:
                # Проверяем, указано ли type = layers
                if stripped.lower().startswith("type") and "layers" not in stripped.lower():
                    # Это не тот контракт, который нас интересует — выходим из режима
                    result_lines.append(line)
                    in_layers_contract = False
                    in_layers_block = False
                    continue

                # Находим строку "layers:" — именно здесь мы будем подменять содержимое
                if stripped.lower().startswith("layers:"):
                    # Записываем сам заголовок блока слоёв
                    result_lines.append("layers:\n")
                    # Далее — добавляем слои из solidconfig.json (по одному на строку с отступом)
                    for layer in layer_names:
                        result_lines.append(f"    {layer}\n")
                    # Переходим в режим "внутри блока слоёв" — остальные старые слои будем пропускать
                    in_layers_block = True
                    continue

                # Если мы внутри блока "layers:", пропускаем старые строки слоёв, пока не встретим новую секцию
                if in_layers_block:
                    if stripped.startswith("[importlinter:contract:") or stripped.startswith("[importlinter]"):
                        # Это начало новой секции — выходим из блока и обрабатываем эту строку как новую секцию
                        in_layers_contract = stripped.startswith("[importlinter:contract:")
                        in_layers_block = False
                        result_lines.append(line)
                    # Если это просто старые строки слоёв — пропускаем их (уже заменили)
                    continue

                # Если мы внутри нужного контракта, но не в блоке слоёв — просто копируем строки
                result_lines.append(line)
                # Если встретим новую секцию контракта, выходим из режима
                if stripped.startswith("[importlinter:contract:") and not stripped.lower().startswith("layers"):
                    in_layers_contract = False
                    in_layers_block = False
                continue

            # Всё, что вне нужного контракта — копируем без изменений
            result_lines.append(line)

        # Если вдруг контракт с type = layers не найден — просто копируем исходный файл
        if not result_lines:
            result_lines = lines

        with open(out_path, "w", encoding="utf-8") as f:
            f.writelines(result_lines)

    @staticmethod
    def _parse_contract_stats(output: str, linting_passed: bool) -> tuple[int, int]:
        """
        Пытается вытащить из вывода строки вида 'Contracts: 1 kept, 0 broken.'
        или похожие вариации. В крайнем случае — fallback: 1 kept/1 broken.
        """
        kept = 0
        broken = 0

        stats_match = re.search(
            r"(?:contracts?\s*:?[^0-9]*kept[^0-9]*([0-9]+)[^0-9]*broken[^0-9]*([0-9]+))",
            output,
            re.IGNORECASE,
        )
        if stats_match:
            kept = int(stats_match.group(1))
            broken = int(stats_match.group(2))
        else:
            if linting_passed:
                kept = 1
            else:
                broken = 1

        return kept, broken

    @staticmethod
    def _error_message(msg: str) -> Dict[str, Any]:
        # Унифицированный формат ошибки адаптера
        return {
            "is_success": False,
            "error": msg,
            "contracts_checked": 0,
            "broken_contracts": 0,
            "kept_contracts": 0,
            "violations": [],
            "raw_output": "",
        }