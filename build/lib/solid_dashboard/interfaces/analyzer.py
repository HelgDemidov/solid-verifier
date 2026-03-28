from typing import Protocol, runtime_checkable, Any, Dict

@runtime_checkable
class IAnalyzer(Protocol):
    @property
    def name(self) -> str:
        ...
    
    def run(
        self,
        target_dir: str,
        context: Dict[str, Any],
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        
        """
        Анализирует директорию target_dir.
        context: результаты работы предыдущих адаптеров (передаются по цепочке).
        Возвращает словарь с результатами анализа.
        """
        ...
