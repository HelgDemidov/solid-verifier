# комментарий (ru): Адаптер import-linter через Python API use_cases.create_report() без subprocess

from __future__ import annotations

import io
import os   # комментарий (ru): смена рабочей директории
import sys  # комментарий (ru): управление sys.path
import re   # комментарий (ru): для поиска имени недостающего модуля в ошибке
from contextlib import redirect_stdout
from typing import Any, Dict, List

from tools.solid_dashboard.solid_dashboard.interfaces.analyzer import (
    IAnalyzer,
)  # комментарий (ru): общий протокол адаптеров пайплайна

_ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


class ImportLinterAdapter(IAnalyzer):
    """Адаптер для запуска import-linter через Python API."""

    @property
    def name(self) -> str:
        # комментарий (ru): ключ результата в итоговом JSON-отчёте
        return "import_linter"

    def run(self, target_dir: str, context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        # комментарий (ru): нормализуем пути: берём абсолютный корень проекта
        project_root = str(os.path.dirname(os.path.abspath(target_dir)))
        importlinter_config_path = os.path.join(project_root, ".importlinter")

        if not os.path.exists(importlinter_config_path):
            return self._error(f".importlinter not found at: {importlinter_config_path}")

        # комментарий (ru): сохраняем состояние процесса
        saved_cwd = os.getcwd()
        saved_sys_path = list(sys.path)

        try:
            # комментарий (ru): переходим в корень проекта (fix от багов путей)
            os.chdir(project_root)
            if project_root not in sys.path:
                sys.path.insert(0, project_root)

            from importlinter.application import use_cases
            from importlinter.application import rendering
            from importlinter import configuration

            # комментарий (ru): инициализация окружения линтера
            configuration.configure()

            # комментарий (ru): читаем UserOptions
            try:
                user_options = use_cases.read_user_options(
                    config_filename=importlinter_config_path
                )
            except Exception as exc:
                return self._error(f"Failed parsing .importlinter: {exc}")

            # комментарий (ru): КЛЮЧЕВАЯ нормализация root_packages (защита от бага 'a')
            session_opts = user_options.session_options
            raw_pkgs = session_opts.get("root_packages", "")

            if isinstance(raw_pkgs, str):
                session_opts["root_packages"] = [
                    p.strip() for p in raw_pkgs.replace(",", "\n").splitlines() if p.strip()
                ]
            elif isinstance(raw_pkgs, (tuple, set)):
                session_opts["root_packages"] = list(raw_pkgs)

            # комментарий (ru): регистрируем контракты
            try:
                use_cases._register_contract_types(user_options)
            except Exception as exc:
                return self._error(f"Registration error: {exc}")

            # комментарий (ru): строим граф и прогоняем контракты (напрямую, без обертки lint_imports)
            try:
                report = use_cases.create_report(user_options=user_options)
            except ValueError as exc:
                error_msg = str(exc)
                hint = ""
                match_module = re.search(r"module\s+([a-zA-Z0-9_.]+)\s+does not exist", error_msg)
                match_package = re.search(r"Could not find package\s+'([a-zA-Z0-9_.]+)'", error_msg)
                
                if match_module:
                    hint = f" HINT: Missing '__init__.py' in {match_module.group(1).replace('.', '/')}"
                elif match_package:
                    hint = f" HINT: Missing '{match_package.group(1)}/__init__.py'"

                return self._error(f"Graph error: {error_msg}.{hint}")
            except Exception as exc:
                return self._error(f"Graph error: {exc}")

            # комментарий (ru): Если граф построен, используем официальный рендерер линтера, 
            # чтобы он сам сгенерировал текст со всеми деталями нарушений.
            f = io.StringIO()
            with redirect_stdout(f):
                rendering.render_report(report)
            
            # комментарий (ru): очищаем вывод рендерера
            console_output = f.getvalue()
            clean_output = _ANSI_ESCAPE.sub("", console_output)

            # комментарий (ru): извлекаем имена нарушенных контрактов для поля violations
            violations: List[str] = []
            for contract, check in report.get_contracts_and_checks():
                if not check.kept:
                    violations.append(contract.name)

            return {
                "is_success": not report.contains_failures,
                "contracts_checked": report.kept_count + report.broken_count,
                "broken_contracts": report.broken_count,
                "kept_contracts": report.kept_count,
                "violations": violations,
                "module_count": report.module_count,
                "raw_output": clean_output.strip() or "No output generated.",
            }

        finally:
            # комментарий (ru): восстанавливаем состояние
            os.chdir(saved_cwd)
            sys.path[:] = saved_sys_path

    @staticmethod
    def _error(message: str) -> Dict[str, Any]:
        return {
            "is_success": False,
            "error": message,
            "contracts_checked": 0,
            "broken_contracts": 0,
            "kept_contracts": 0,
            "violations": [],
            "module_count": 0,
            "raw_output": "",
        }
