import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from solid_dashboard.interfaces.analyzer import IAnalyzer
import grimp


class ImportGraphAdapter(IAnalyzer):
    """
    Адаптер для построения графа архитектурных слоёв на основе grimp.

    Использует тот же движок, что и import-linter. Это снижает риск
    расхождения между визуальным графом и контрактной проверкой.
    """

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
        Основной метод адаптера.

        Делает три шага:
        1. строит граф импортов через grimp;
        2. сопоставляет модули с архитектурными слоями;
        3. считает метрики устойчивости по слоям.
        """
        # определяем путь к анализируемому пакету и его корневое имя
        target_path = Path(target_dir).resolve()
        package_name = target_path.name

        # читаем конфиг внутренних слоёв
        layer_config: Dict[str, List[str]] = config.get("layers", {})
        if not layer_config:
            return {
                "nodes": [],
                "edges": [],
                "error": "no layer configuration found in solidconfig.json",
            }

        # читаем необязательный конфиг внешних библиотек
        # пример:
        # "external_layers": {
        #     "db_libs": ["sqlalchemy"],
        #     "web_libs": ["fastapi", "starlette", "pydantic"]
        # }
        external_layer_config: Dict[str, List[str]] = config.get(
            "external_layers", {}
        )

        # нормализуем конфиг слоёв:
        # "routers" -> "app.routers"
        # "services" -> "app.services"
        normalized_layers = self._normalize_layer_config(
            layer_config, package_name
        )

        # временно добавляем родительскую директорию в sys.path,
        # чтобы grimp гарантированно нашёл пакет
        parent_dir = str(target_path.parent)
        added_to_path = False
        if parent_dir not in sys.path:
            sys.path.insert(0, parent_dir)
            added_to_path = True

        try:
            # строим граф импортов тем же движком, что использует import-linter
            # include_external_packages нужен для учёта third-party зависимостей
            graph = grimp.build_graph(
                package_name,
                include_external_packages=True,
            )

            # строим агрегированный граф слоёв и считаем stability-метрики
            nodes, edges = self._build_layer_graph(
                graph=graph,
                layer_config=normalized_layers,
                external_layer_config=external_layer_config,
            )

            return {
                "nodes": nodes,
                "edges": edges,
                "debug_info": {
                    "package": package_name,
                    "total_modules": len(graph.modules),
                    "layer_prefixes_used": normalized_layers,
                    "external_layer_prefixes_used": external_layer_config,
                },
            }

        except Exception as exc:
            return {
                "nodes": [],
                "edges": [],
                "error": str(exc),
            }

        finally:
            # аккуратно откатываем sys.path к исходному состоянию
            if added_to_path and parent_dir in sys.path:
                sys.path.remove(parent_dir)

    def _normalize_layer_config(
        self,
        layer_config: Dict[str, Any],
        package_name: str,
    ) -> Dict[str, List[str]]:
        """
        Нормализует конфиг внутренних слоёв.

        Поддерживает оба формата:
        - "routers": "routers"
        - "routers": ["routers"]

        На выходе всегда возвращает:
        - "routers": ["app.routers"]
        """
        normalized: Dict[str, List[str]] = {}
        package_prefix = f"{package_name}."

        for layer_name, raw_value in layer_config.items():
            # приводим значение слоя к списку строк
            if isinstance(raw_value, str):
                paths = [raw_value]
            elif isinstance(raw_value, list):
                paths = [p for p in raw_value if isinstance(p, str)]
            else:
                # некорректный тип silently пропускаем,
                # чтобы не ломать весь адаптер
                paths = []

            fixed_paths: List[str] = []

            for path in paths:
                cleaned_path = path.strip()
                if not cleaned_path:
                    continue

                # если путь уже полный, не меняем его
                if (
                    cleaned_path == package_name
                    or cleaned_path.startswith(package_prefix)
                ):
                    fixed_paths.append(cleaned_path)
                else:
                    fixed_paths.append(f"{package_name}.{cleaned_path}")

            normalized[layer_name] = fixed_paths

        return normalized

    def _build_layer_graph(
        self,
        graph: grimp.ImportGraph,
        layer_config: Dict[str, List[str]],
        external_layer_config: Dict[str, List[str]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
        """
        Преобразует граф модулей в граф слоёв.

        Возвращает:
        - nodes: слои с метриками ca, ce, instability;
        - edges: уникальные направленные связи между слоями.
        """
        # собираем полный список слоёв:
        # сначала внутренние, затем внешние
        all_layer_names: List[str] = list(layer_config.keys())
        all_layer_names.extend(external_layer_config.keys())

        # храним только уникальные межслоевые зависимости
        layer_edges: Set[Tuple[str, str]] = set()

        # обходим все найденные grimp модули
        for module_name in graph.modules:
            # импортирующий модуль должен принадлежать одному из наших слоёв
            importer_layer = self._resolve_internal_layer(
                module_name, layer_config
            )
            if not importer_layer:
                continue

            # получаем прямые импорты текущего модуля
            try:
                imported_modules = graph.find_modules_directly_imported_by(
                    module_name
                )
            except Exception:
                # защитный сценарий на случай краевых проблем grimp
                continue

            for imported_module_name in imported_modules:
                # сначала ищем внутренний слой
                imported_layer = self._resolve_internal_layer(
                    imported_module_name, layer_config
                )

                # если не нашли, ищем внешний слой
                if not imported_layer and external_layer_config:
                    imported_layer = self._resolve_external_layer(
                        imported_module_name,
                        external_layer_config,
                    )

                # если модуль не относится ни к одному известному слою,
                # просто пропускаем его
                if not imported_layer:
                    continue

                # петли слой -> тот же слой не добавляем
                if importer_layer != imported_layer:
                    layer_edges.add((importer_layer, imported_layer))

        # после того как все рёбра собраны, считаем метрики устойчивости
        nodes = self._build_nodes_with_stability(
            layer_names=all_layer_names,
            layer_edges=layer_edges,
        )

        # приводим рёбра к json-совместимому формату
        edges = [
            {"source": source, "target": target}
            for source, target in sorted(layer_edges)
        ]

        return nodes, edges

    def _build_nodes_with_stability(
        self,
        layer_names: List[str],
        layer_edges: Set[Tuple[str, str]],
    ) -> List[Dict[str, Any]]:
        """
        Считает для каждого слоя метрики stability.

        ca:
            сколько слоёв зависит от данного слоя
        ce:
            от скольких слоёв зависит данный слой
        instability:
            ce / (ca + ce), диапазон от 0.0 до 1.0
        """
        nodes: List[Dict[str, Any]] = []

        for layer_name in layer_names:
            # ce = количество исходящих зависимостей слоя
            ce = len(
                {
                    target
                    for source, target in layer_edges
                    if source == layer_name
                }
            )

            # ca = количество входящих зависимостей слоя
            ca = len(
                {
                    source
                    for source, target in layer_edges
                    if target == layer_name
                }
            )

            # instability по роберту мартину
            if ca + ce > 0:
                instability = round(ce / (ca + ce), 2)
            else:
                instability = 0.0

            nodes.append(
                {
                    "id": layer_name,
                    "label": layer_name,
                    "ca": ca,
                    "ce": ce,
                    "instability": instability,
                }
            )

        return nodes

    def _resolve_internal_layer(
        self,
        module_name: str,
        layer_config: Dict[str, List[str]],
    ) -> Optional[str]:
        """
        Ищет внутренний слой для модуля.

        Пример:
        - модуль "app.services.user_service"
        - путь слоя "app.services"
        - результат: "services"
        """
        for layer_name, paths in layer_config.items():
            for path in paths:
                if module_name == path or module_name.startswith(f"{path}."):
                    return layer_name
        return None

    def _resolve_external_layer(
        self,
        module_name: str,
        external_layer_config: Dict[str, List[str]],
    ) -> Optional[str]:
        """
        Ищет внешний слой для third-party модуля.

        Пример:
        - модуль "sqlalchemy.orm"
        - внешний слой "db_libs": ["sqlalchemy"]
        - результат: "db_libs"
        """
        for layer_name, package_prefixes in external_layer_config.items():
            for package_prefix in package_prefixes:
                if (
                    module_name == package_prefix
                    or module_name.startswith(f"{package_prefix}.")
                ):
                    return layer_name
        return None