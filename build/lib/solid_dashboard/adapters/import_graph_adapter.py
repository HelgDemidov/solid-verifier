import ast
import os
from typing import Dict, Any, List, Set, Tuple


class ImportGraphAdapter:
    @property
    def name(self) -> str:
        return "import_graph"

    def run(
        self,
        target_dir: str,
        context: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Строит граф импортов между внутренними модулями проекта.
        Слои и игнорируемые директории берутся из config.
        """
        # Читаем конфигурацию
        package_root = config.get("package_root", "app")
        ignore_dirs = set(config.get("ignore_dirs", []))

        # Инициализация структур данных
        internal_modules: Dict[str, str] = {}
        errors: List[str] = []
        import_relations: List[tuple[str, str]] = []

        # 1. Обход файловой системы и сбор всех внутренних модулей
        for root, dirs, files in os.walk(target_dir):
            # Фильтруем директории
            dirs[:] = [d for d in dirs if d not in ignore_dirs]

            for filename in files:
                if not filename.endswith(".py"):
                    continue

                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, start=target_dir)

                try:
                    module_name = self._module_name_from_path(rel_path, package_root)
                    internal_modules[module_name] = full_path
                except Exception as e:
                    # Исправлено: добавляем строку, как ожидает List[str]
                    errors.append(f"Failed to resolve module for {rel_path}: {e}")

        # Формируем множество всех внутренних модулей для быстрой проверки
        internal_set: Set[str] = set(internal_modules.keys())

        # 2. AST-анализ: парсим каждый файл и ищем импорты
        for module_name, file_path in internal_modules.items():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    source_code = f.read()

                tree = ast.parse(source_code, filename=file_path)

                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            normalized = self._normalize_import_name(alias.name, package_root)
                            import_relations.append((module_name, normalized))

                    elif isinstance(node, ast.ImportFrom):
                        resolved_names = self._resolve_import_from(module_name, node, package_root)
                        for r_name in resolved_names:
                            normalized = self._normalize_import_name(r_name, package_root)
                            import_relations.append((module_name, normalized))

            except Exception as e:
                # Исправлено: добавляем строку, а не dict
                errors.append(f"Parse error in {file_path}: {str(e)}")

        # 3. Сборка графа (Nodes & Edges)
        nodes: List[Dict[str, Any]] = []
        for mod_name, path in internal_modules.items():
            layer = self._detect_layer(mod_name, config)
            # label — имя без package_root (например, routers.users)
            label = ".".join(mod_name.split(".")[1:]) if "." in mod_name else mod_name

            nodes.append({
                "id": mod_name,
                "label": label,
                "path": path,
                "layer": layer,
            })

        # Устраняем дубликаты рёбер через множество
        edge_set: Set[tuple[str, str]] = set()

        for src, dst in import_relations:
            # Оставляем только внутренние зависимости
            if dst in internal_set and src != dst:
                edge_set.add((src, dst))

        edges: List[Dict[str, Any]] = [
            {"from": src, "to": dst, "kind": "internal"}
            for (src, dst) in sorted(edge_set)
        ]

        return {
            "nodes": nodes,
            "edges": edges,
            "errors": errors,
            "edges_count": len(edges),
        }

    def _module_name_from_path(self, rel_path: str, package_root: str) -> str:
        """
        Преобразует относительный путь вроде 'routers\\users.py' в 'app.routers.users'.
        """
        without_ext = os.path.splitext(rel_path)[0]
        parts = without_ext.split(os.sep)
        return ".".join([package_root] + parts)

    def _normalize_import_name(self, name: str, package_root: str) -> str:
        """
        Приводит имя импорта к каноническому виду модуля:
        - если начинается с package_root (app.routers.users) -> оставляем как есть;
        - если начинается с 'app.' при другом названии корня -> корректируем префикс;
        - иначе:
            * если в имени есть точка, считаем его внешним (fastapi.FastAPI и т.п.)
              и возвращаем как есть;
            * если точек нет, считаем что это относительный импорт внутри пакета и
              добавляем package_root.
        Внешние пакеты всё равно отфильтруются по internal_set.
        """
        if name.startswith(f"{package_root}."):
            return name
        if name.startswith("app.") and package_root != "app":
            return name.replace("app.", f"{package_root}.", 1)

        # Если в имени есть точка, это, скорее всего, fully-qualified имя внешнего пакета
        # (fastapi.FastAPI, httpx.AsyncClient и т.п.) — оставляем как есть.
        if "." in name:
            return name

        # Иначе это короткое имя внутри нашего корня (routers, services, core...)
        return f"{package_root}.{name}"

    def _resolve_import_from(
        self,
        current_module: str,
        node: ast.ImportFrom,
        package_root: str,
    ) -> List[str]:
        """
        Разрешает конструкции вида:
        - from app.routers import articles         -> app.routers.articles
        - from app.infrastructure.database import async_session_maker -> app.infrastructure.database
        - from . import users                      -> app.routers.users (относительно current_module)
        - from .services import user_service       -> app.services.user_service (упрощённо)
        """
        results: List[str] = []

        current_parts = current_module.split(".")  # ['app', 'routers', 'users']

        # 1. Базовая часть: корень пакета
        base_parts = [package_root]

        # 2. Если указан node.module, используем его как основу
        #    (без добавления package_root второй раз)
        if node.module:
            module_parts = node.module.split(".")
            if module_parts[0] == package_root or module_parts[0] == "app":
                # Полностью квалифицированное имя: app.routers
                base_parts = module_parts
            else:
                # Что-то вроде 'routers' или 'infrastructure.database'
                base_parts = [package_root] + module_parts
        else:
            # from . import users / from ..core import security
            if node.level:
                # Отбрасываем node.level частей справа от current_module
                if node.level >= len(current_parts):
                    base_parts = [package_root]
                else:
                    base_parts = current_parts[:-node.level]
            else:
                # from X import Y без module и без level — редкий кейс, оставляем корень
                base_parts = [package_root]

        for alias in node.names:
            # Особый случай: node.module указывает уже на конкретный модуль,
            # а alias — на объект внутри него (функция/класс).
            # Пример: from app.infrastructure.database import async_session_maker
            # Для графа импортов нас интересует сам модуль database, а не объект.
            if node.module:
                # Проверим, выглядит ли node.module как полный путь до модуля
                # (в нашей архитектуре всё под package_root.* считается модулем)
                module_name = ".".join(base_parts)
                results.append(module_name)
            else:
                # Случай: from . import users  -> base_parts + ['users']
                full_name = ".".join(base_parts + [alias.name])
                results.append(full_name)

        return results

    def _detect_layer(self, module_name: str, config: Dict[str, Any]) -> str:
        """
        Определяет слой для модуля по его имени и конфигу.
        Пример: module_name = "app.routers.users"
        layers = {"routers": "routers", "services": "services", ...}
        """
        layers_cfg = config.get("layers", {})
        # module_name без package_root: "routers.users"
        parts = module_name.split(".")
        # Ожидаем, что module_name уже включает package_root, например "app.routers.users"
        if not layers_cfg or len(parts) < 2:
            return "other"

        # Берем сегмент после корня пакета: "routers"|"services"|...
        first_after_root = parts[1]

        for layer_name, dirname in layers_cfg.items():
            if first_after_root == dirname:
                return layer_name

        return "other"
