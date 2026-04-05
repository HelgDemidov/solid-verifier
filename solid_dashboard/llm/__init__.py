# Публичный API llm-пакета — экспортируем все контракты одним импортом.
# Внешний код импортирует типы и функции отсюда, а не напрямую из подмодулей,
# чтобы внутренняя структура пакета могла меняться без поломки импортов снаружи.

# --- Типы и контракты данных (из types.py) ---
from .types import (
    MethodSignature,
    ClassInfo,
    InterfaceInfo,
    ProjectMap,
    LlmCandidate,
    HeuristicResult,
    LlmConfig,
    LlmAnalysisInput,
    LlmAnalysisOutput,
    LlmMetadata,
    Finding,
    FindingDetails,
    SourceType,
    CandidateType,
    SeverityLevel,
    LlmResponse,
    ParseStatus,
    ParseResult,
)

# --- Функции пайплайна (из подмодулей) ---
from .analysis.ast_parser import build_project_map

# Единый __all__ — объединяет все публичные имена пакета.
# Разбит на смысловые секции для читаемости.
__all__ = [
    # Примитивные типы и литералы
    "SourceType",
    "CandidateType",
    "SeverityLevel",
    "ParseStatus",
    # Структуры данных ProjectMap
    "MethodSignature",
    "ClassInfo",
    "InterfaceInfo",
    "ProjectMap",
    # Структуры результатов анализа
    "Finding",
    "FindingDetails",
    "HeuristicResult",
    "LlmCandidate",
    # LLM-контракты
    "LlmConfig",
    "LlmAnalysisInput",
    "LlmAnalysisOutput",
    "LlmMetadata",
    "LlmResponse",
    "ParseResult",
    # Функции пайплайна
    "build_project_map",
]
