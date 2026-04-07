# ===================================================================================================
# Адаптер Import Linter (Import Linter Adapter)
#
# Ключевая роль: Проверка строгих архитектурных контрактов (API Layered Architecture) с использованием утилиты import-linter.
#
# Основные архитектурные задачи:
# 1. Динамическая генерация временного конфига (.importlinter_auto) на основе
#    базового файла .importlinter и актуального списка слоев из solid_config.json.
# 2. Изолированный запуск import-linter CLI в подпроцессе с передачей правильного
#    контекста (PYTHONPATH), охватывающего директорию анализа.
# 3. Применение фильтра import_linter_ignore_subpackages (настройка ignore_imports) для
#    явного исключения субпакетов из архитектурных проверок.
# 4. Парсинг текстового вывода линтера (ANSI-очистка) для подсчета нарушенных/
#    соблюденных контрактов и извлечения списка конкретных нарушений.
# ===================================================================================================


import configparser  # стандартный INI-парсер для работы с .importlinter
import os             # работа с путями и файлами
import re             # разбор текста и ANSI-кодов
import subprocess     # запуск lint-imports как отделэного процесса
from typing import Any, Dict, List  # типы для аннотаций

from solid_dashboard.interfaces.analyzer import IAnalyzer  # базовый интерфейс адаптера

# Регулярное выражение для очистки вывода от ANSI-кодов (цветной вывод, рамки и т.п.)
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class ImportLinterAdapter(IAnalyzer):
    # Синхронизирует базовый конфигурационный файл .importlinter с единой моделью solid_config.json:
    # - Читает существующий базовый файл .importlinter через configparser (нечувствительно к порядку полей)
    # - Динамически перезаписывает параметр root_packages под целевую директорию (package_name)
    # - Обновляет архитектурный контракт (блок 'layers') актуальными слоями проекта во всех контрактах типа layers
    # - Исключает субпакеты из import_linter_ignore_subpackages через ignore_imports
    # - Сохраняет результат во временный файл (например, .importlinter_auto_app)
    # - Запускает lint-imports --config <temp_file> и безопасно удаляет его после работы

    @property
    def name(self) -> str:
        # Имя адаптера для JSON-отчета
        return "import_linter"

    def run(self, target_dir: str, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        # параметр context требуется интерфейсом IAnalyzer, в этом адаптере не используется
        _ = context

        target_path = os.path.abspath(target_dir)
        project_root = os.path.dirname(target_path)
        # Извлекаем реальное имя пакета (например 'app' или 'src')
        package_name = os.path.basename(target_path)

        base_config_path = os.path.join(project_root, ".importlinter")
        # Делаем имя временного файла уникальным для предотвращения коллизий
        temp_config_path = os.path.join(project_root, f".importlinter_auto_{package_name}")

        if not os.path.exists(base_config_path):
            return self._error_message(f".importlinter not found at {base_config_path}")

        try:
            # Генерируем синхронизированный временный конфиг через configparser
            self.generate_synced_config(
                base_config_path=base_config_path,
                solid_config=config,
                outpath=temp_config_path,
                package_name=package_name,
            )

            # Пробрасываем корень проекта в PYTHONPATH для корректного разрешения импортов
            env = os.environ.copy()
            if "PYTHONPATH" in env:
                env["PYTHONPATH"] = f"{project_root}{os.pathsep}{env['PYTHONPATH']}"
            else:
                env["PYTHONPATH"] = project_root

            cmd = ["lint-imports", "--config", temp_config_path]
            completed = subprocess.run(
                cmd,
                cwd=project_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            raw_console = completed.stdout or completed.stderr or ""
            clean_output = ANSI_ESCAPE.sub("", raw_console).strip()

            # returncode=0 — все контракты соблюдены, returncode=1 — есть нарушения;
            # любой другой код — ошибка среды выполнения (не найден пакет, синтаксис конфига и т.п.)
            if completed.returncode not in (0, 1):
                return self._error_message(
                    f"lint-imports exited with code {completed.returncode}.\n{clean_output}"
                )

            linting_passed = completed.returncode == 0
            kept, broken = self._parse_contract_stats(clean_output, linting_passed)

            # Извлекаем имена нарушенных контрактов из вывода линтера
            violations: List[str] = []
            for line in clean_output.splitlines():
                stripped = line.strip()
                if stripped.endswith(" BROKEN"):
                    name_part = stripped[: -len(" BROKEN")].rstrip()
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
            return self._error_message(
                "Command lint-imports not found. Ensure import-linter is installed."
            )
        except Exception as exc:
            return self._error_message(f"ImportLinterAdapter failed: {exc}")
        finally:
            # Гарантированно удаляем временный файл даже при исключении
            if os.path.exists(temp_config_path):
                try:
                    os.remove(temp_config_path)
                except OSError:
                    pass

    def generate_synced_config(
        self,
        base_config_path: str,
        solid_config: Dict[str, Any],
        outpath: str,
        package_name: str,
    ) -> None:
        """
        Читает базовый .importlinter через configparser, заменяет root_packages
        на package_name, обновляет блок layers во всех контрактах типа layers,
        добавляет ignore_imports из import_linter_ignore_subpackages
        и сохраняет результат в outpath.

        Семантическое разделение полей конфига:
          ignore_dirs                    — filesystem-фильтр для всех адаптеров
          import_linter_ignore_subpackages — Python import paths только для этого адаптера

        import_linter_ignore_subpackages опциональное поле: если отсутствует —
        ignore_imports не генерируется, адаптер работает в штатном режиме.
        """
        cfg = configparser.RawConfigParser()
        # Сохраняем регистр ключей — configparser по умолчанию приводит к нижнему
        cfg.optionxform = str  # type: ignore[assignment]
        cfg.read(base_config_path, encoding="utf-8")

        if cfg.has_section("importlinter"):
            # Записываем root_packages как multiline INI-значение — критично для import-linter:
            # при однострочном значении ('app') import-linter итерирует строку посимвольно
            # и получает ['a','p','p']; multiline-форма гарантирует разбор через splitlines()
            cfg.set("importlinter", "root_packages", f"\n    {package_name}")
            # unmatched_ignore_imports=warn: страховочный слой против ошибок при
            # рассинхронизации конфига со структурой пакета (паттерн без совпадений → warn, не error)
            cfg.set("importlinter", "unmatched_ignore_imports", "warn")

        layer_config: Dict[str, Any] = solid_config.get("layers", {})
        layer_names = list(layer_config.keys())

        # Ичитываем опциональный список субпакетов для исключения из архитектурных контрактов.
        # Отдельное поле (ignore_dirs не используется): filesystem и import paths — разные семантики
        linter_ignore_raw = solid_config.get("import_linter_ignore_subpackages") or []
        linter_ignore = [d.strip() for d in linter_ignore_raw if d and d.strip()]

        # Итерируемся по всем секциям — обрабатываем каждый контракт независимо
        for section in cfg.sections():
            if not section.startswith("importlinter:contract:"):
                continue

            # Определяем тип контракта; пропускаем секции без поля type
            try:
                contract_type = cfg.get(section, "type").strip().lower()
            except configparser.NoOptionError:
                continue

            # Обновляем только контракты типа layers; forbidden/independence не трогаем
            if contract_type != "layers" or not layer_names:
                continue

            # Формируем multiline-строку слоев в формате INI (отступ = 4 пробела);
            # имена слоев без префикса пакета — containers в .importlinter задает пространство имен
            layers_value = "\n" + "\n".join(
                f"    {layer}" for layer in layer_names
            )
            cfg.set(section, "layers", layers_value)

            # Генерируем ignore_imports только если пользователь явно указал субпакеты
            if linter_ignore:
                ignore_lines = []
                for d in linter_ignore:
                    # Исключаем как исходящие, так и входящие импорты исключаемого субпакета
                    ignore_lines.append(f"    {package_name}.{d}.* -> *")
                    ignore_lines.append(f"    * -> {package_name}.{d}.*")
                cfg.set(section, "ignore_imports", "\n" + "\n".join(ignore_lines))

        # Записываем итоговый конфиг во временный файл
        with open(outpath, "w", encoding="utf-8") as f:
            cfg.write(f)

    @staticmethod
    def _parse_contract_stats(output: str, linting_passed: bool) -> tuple[int, int]:
        """
        Извлекает количество kept/broken контрактов из строки вида
        'Contracts: 1 kept, 0 broken.' или похожих вариаций.
        Fallback при несовпадении: 1 kept или 1 broken по returncode.
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
            # Fallback: формат вывода не распознан, но returncode однозначен
            if linting_passed:
                kept = 1
            else:
                broken = 1

        return kept, broken

    @staticmethod
    def _error_message(msg: str) -> Dict[str, Any]:
        # Унифицированный формат ошибки адаптера — совместим с IAnalyzer
        return {
            "is_success": False,
            "error": msg,
            "contracts_checked": 0,
            "broken_contracts": 0,
            "kept_contracts": 0,
            "violations": [],
            "raw_output": "",
        }
