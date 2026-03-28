# комментарий (ru): Адаптер для анализа графа вызовов (call graph) с помощью pyan3.
# Цель: построить ориентированный граф "кто кого вызывает" внутри пакета app,
# отфильтровать слой routers (FastAPI endpoints) и вернуть данные в JSON-формате
# для последующей визуализации/AI-анализа (OCP/LSP, поиск мёртвого кода и т.д.).

from __future__ import annotations

import os  # комментарий (ru): работа с файловой системой и путями
import re  # комментарий (ru): регулярки для парсинга вывода pyan3
import subprocess  # комментарий (ru): запуск pyan3 как CLI-инструмента
from typing import Any, Dict, List, Set

from solid_dashboard.interfaces.analyzer import IAnalyzer  # общий протокол адаптеров пайплайна 

class Pyan3Adapter(IAnalyzer):
    """Адаптер для pyan3: строит граф вызовов на уровне функций/методов."""

    @property
    def name(self) -> str:
        # комментарий (ru): ключ, под которым результат адаптера попадает в итоговый JSON
        return "pyan3"

    def run(
        self,
        target_dir: str,
        context: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        # комментарий (ru): target_dir в нашем пайплайне — это "./app".
        # Нам нужно запустить pyan3 по этому каталогу и распарсить его текстовый вывод.
        project_root = os.path.dirname(os.path.abspath(target_dir))

        # комментарий (ru): собираем абсолютный путь к анализируемому пакету
        app_dir = os.path.abspath(target_dir)

        # комментарий (ru): pyan3 мы будем вызывать из корня проекта, чтобы относительные
        # пути в его выводе были стабильными и повторяемыми.
        saved_cwd = os.getcwd()
        try:
            os.chdir(project_root)

            # комментарий (ru): Формируем команду CLI.
            # Выбор флагов:
            #   --uses       : только рёбра "использует/вызывает", без "определяет";
            #   --no-defines : не добавлять рёбра определения (упрощаем граф);
            #   --text       : текстовый формат вывода, легко парсить;
            #   --quiet      : подавить лишний шум/diagnostics (если есть).
            #
            # Можно добавить --depth=3, но пока возьмём дефолт (полный уровень методов),
            # чтобы не терять детали для последующего анализа.
            cmd = [
                "pyan3",
                app_dir,
                "--uses",
                "--no-defines",
                "--text",
                "--quiet",
            ]

            try:
                # комментарий (ru): запускаем pyan3 как внешний процесс.
                # Используем capture_output=True, text=True для получения stdout как строки.
                completed = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                )
            except FileNotFoundError:
                # комментарий (ru): pyan3 не установлен в окружении — возвращаем
                # аккуратную ошибку, чтобы пайплайн не падал целиком.
                return self._error(
                    "pyan3 executable not found. "
                    "Make sure 'pyan3' is installed in the virtual environment."
                )

            if completed.returncode != 0:
                # комментарий (ru): pyan3 завершился с ошибкой — возвращаем stderr,
                # чтобы пользователь видел, что пошло не так (синтаксическая ошибка и т.п.)
                stderr = completed.stderr.strip() or "Unknown pyan3 error."
                return self._error(f"pyan3 failed with exit code {completed.returncode}: {stderr}")

            raw_output = completed.stdout

            # комментарий (ru): новый парсер текстового вывода pyan3.
            # Формат в режиме --text:
            #   NodeName
            #       [U] UsedNode1
            #       [U] UsedNode2
            #   OtherNode
            #       [U] ...
            #
            # Строка БЕЗ ведущих пробелов = текущий источник (current_src).
            # Строка С ведущими пробелами и префиксом "[U]" = ребро
            #   current_src -> used_node.

            nodes: Set[str] = set()
            edges: List[Dict[str, str]] = []

            current_src: str | None = None

            for line in raw_output.splitlines():
                # комментарий (ru): сохраняем оригинал для анализа отступа,
                # но часть логики будем делать на stripped-версии
                if not line.strip():
                    continue  # пустые строки пропускаем

                # строка без начального пробела → новый текущий узел
                if not line.startswith((" ", "\t")):
                    current_src = line.strip()
                    nodes.add(current_src)
                    continue

                # если мы здесь, значит строка с отступом
                stripped = line.strip()
                # интересуют только строки вида "[U] Something"
                if not stripped.startswith("[U]"):
                    continue

                # вырезаем метку "[U]" и берём имя зависимого узла
                used_name = stripped[len("[U]") :].strip()
                if not used_name or current_src is None:
                    continue

                # добавляем ребро current_src -> used_name
                nodes.add(used_name)
                edges.append({"from": current_src, "to": used_name})

            # комментарий (ru): на этом этапе nodes/edges содержат граф для ВСЕХ узлов,
            # включая endpoints в routers. Дальше применяем фильтрацию routers.

            router_nodes: Set[str] = set()
            for node in nodes:
                # считаем узел "router-узлом", если он явно относится к модулю app.routers
                # или к функциям из этих модулей (по имени вида "app.routers.users", "app.routers.users.get_me" и т.п.)
                if node.startswith("app.routers") or ".routers." in node:
                    router_nodes.add(node)

            if router_nodes:
                edges = [
                    e
                    for e in edges
                    if e["from"] not in router_nodes and e["to"] not in router_nodes
                ]

                used_nodes: Set[str] = set()
                for e in edges:
                    used_nodes.add(e["from"])
                    used_nodes.add(e["to"])
                nodes = used_nodes

            # комментарий (ru): дедупликация рёбер.
            # Pyan3 иногда даёт несколько одинаковых строк [U] для одной пары узлов.
            # Чтобы не раздувать граф, превращаем список рёбер в множество пар.
            unique_edges: Set[tuple[str, str]] = set()
            for e in edges:
                unique_edges.add((e["from"], e["to"]))

            edges = [{"from": src, "to": dst} for src, dst in unique_edges]

            # комментарий (ru): health-check: считаем узлы без входящих рёбер
            # (их никто не вызывает внутри проекта). Это кандидаты на "мёртвый код".
            incoming_count: Dict[str, int] = {n: 0 for n in nodes}
            for e in edges:
                dst = e["to"]
                if dst in incoming_count:
                    incoming_count[dst] += 1

            dead_nodes = sorted(n for n, cnt in incoming_count.items() if cnt == 0)

            node_list = sorted(nodes)

            return {
                "is_success": True,
                "node_count": len(node_list),
                "edge_count": len(edges),
                "nodes": node_list,
                "edges": edges,
                "dead_node_count": len(dead_nodes),
                "dead_nodes": dead_nodes,
                "raw_output": raw_output,
            }

        finally:
            # комментарий (ru): восстанавливаем исходную рабочую директорию,
            # чтобы не ломать окружение для других адаптеров.
            os.chdir(saved_cwd)

    @staticmethod
    def _error(message: str) -> Dict[str, Any]:
        # комментарий (ru): стандартизированный формат ошибки адаптера.
        # Все числовые поля и коллекции присутствуют, чтобы генератор отчёта
        # мог безопасно читать их без дополнительных проверок.
        return {
            "is_success": False,
            "error": message,
            "node_count": 0,
            "edge_count": 0,
            "nodes": [],
            "edges": [],
            "dead_node_count": 0,
            "dead_nodes": [],
            "raw_output": "",
        }
