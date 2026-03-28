from typing import Dict, Any, List
from .interfaces.analyzer import IAnalyzer

def run_pipeline(
    target_dir: str,
    config: Dict[str, Any],
    adapters: List[IAnalyzer],
) -> Dict[str, Any]:
    """
    Запускает все адаптеры по очереди, передавая им context и config.
    """
    # context хранит результаты предыдущих адаптеров
    context: Dict[str, Any] = {}
    results: Dict[str, Any] = {}

    for adapter in adapters:
        result = adapter.run(target_dir, context, config)
        results[adapter.name] = result
        context[adapter.name] = result

    return results
