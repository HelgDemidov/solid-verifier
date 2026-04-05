"""
Интеграционные тесты: сквозной пайплайн build_project_map → identify_candidates.

Проверяет весь основной путь на реальных Python-файлах (smells.py / clean.py):
  - все ожидаемые rule-коды присутствуют в findings
  - чистый код не порождает false positives
  - метаданные каждого Finding корректны
  - список кандидатов сформирован правильно
  - ProjectMap от парсера содержит корректные метаданные
  - сырые LSP-эвристики и дедупликация не теряют findings до финального результата

Запуск:
  pytest tools/solid_verifier/tests/llm/test_heuristics/test_integration_pipeline.py -v
"""
import ast
import textwrap

import pytest

from solid_dashboard.llm.types import HeuristicResult
from solid_dashboard.llm.analysis.ast_parser import build_project_map
from solid_dashboard.llm.heuristics import lsp_h_001, lsp_h_002, identify_candidates
from solid_dashboard.llm.heuristics._runner import _deduplicate_findings

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Фикстуры: один тестовый Python-файл на несколько тестов
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def smelly_project_dir(tmp_path_factory):
    """
    Создает временную директорию с двумя Python-файлами:
    - smells.py — классы с намеренными нарушениями для каждой эвристики
      (DOMAIN-классы, проходящие через весь пайплайн).
    - clean.py — классы без нарушений (для проверки отсутствия false positives).

    scope="module" — файлы создаются один раз для всего модуля тестов.
    """
    base = tmp_path_factory.mktemp("integration_project")

    # --- smells.py: по одному устойчивому нарушению на каждую эвристику ---
    (base / "smells.py").write_text(textwrap.dedent("""
        # Классы-«носители» нарушений для интеграционных тестов.
        # Каждый класс моделирует реальный DOMAIN-кейс, а не CONFIG/INFRA.

        # === Общая доменная база для LSP-сценариев ===
        class BaseSerializer:
            def serialize(self, data):
                return str(data)

        class BaseNotifier:
            def notify(self, message):
                return f"notify:{message}"

        class BaseService:
            def __init__(self):
                self.enabled = True

            def execute(self, payload):
                return payload

        # === LSP-H-001: override метод бросает NotImplementedError ===
        class XmlSerializer(BaseSerializer):
            def __init__(self):
                self.format = "xml"

            def serialize(self, data):
                raise NotImplementedError("XML not supported")

        # === LSP-H-002: override метод с пустым телом ===
        class SilentNotifier(BaseNotifier):
            def __init__(self):
                self.muted = True

            def notify(self, message):
                pass  # Намеренно пустой override

        # === LSP-H-004: __init__ без super().__init__() у DOMAIN-класса ===
        class BrokenService(BaseService):
            def __init__(self):
                # Намеренно не вызываем super().__init__()
                self.service_name = "broken"

            def execute(self, payload):
                return payload

        # === OCP-H-001: цепочка if/elif с isinstance >= 4 ветвей ===
        class Circle:
            pass

        class Square:
            pass

        class Triangle:
            pass

        class Hexagon:
            pass

        class ShapeRenderer:
            def render(self, shape):
                if isinstance(shape, Circle):
                    return "circle"
                elif isinstance(shape, Square):
                    return "square"
                elif isinstance(shape, Triangle):
                    return "triangle"
                elif isinstance(shape, Hexagon):
                    return "hexagon"
                return "unknown"

        # === OCP-H-004: высокая CC + isinstance ===
        class BaseReport:
            pass

        class ComplexProcessor:
            def process(self, item):
                if item.step == "validate":
                    self._validate(item)
                if item.step == "transform":
                    self._transform(item)
                if item.step == "enrich":
                    self._enrich(item)
                if isinstance(item, BaseReport):
                    self._special_report_handling(item)

            def _validate(self, item):
                return None

            def _transform(self, item):
                return None

            def _enrich(self, item):
                return None

            def _special_report_handling(self, item):
                return None

        # === Класс с несколькими нарушениями: высокий приоритет ===
        class HighPrioritySmell(BaseSerializer):
            def __init__(self):
                # LSP-H-004: нет super().__init__()
                self.ready = False

            def serialize(self, data):
                # LSP-H-001: override с NotImplementedError
                raise NotImplementedError
    """), encoding="utf-8")

    # --- clean.py: образцово-показательный код без нарушений ---
    (base / "clean.py").write_text(textwrap.dedent("""
        # Чистый модуль — никаких нарушений OCP/LSP.

        class DataTransformer:
            \"\"\"Трансформирует данные без наследования и type-dispatch.\"\"\"

            def transform(self, data: dict) -> dict:
                result = {}
                for key, value in data.items():
                    result[key] = str(value).strip()
                return result

            def validate(self, data: dict) -> bool:
                return bool(data)

        class StringNormalizer:
            \"\"\"Нормализует строки — простой класс без наследования.\"\"\"

            def normalize(self, text: str) -> str:
                return text.lower().strip()

            def is_empty(self, text: str) -> bool:
                return len(text.strip()) == 0
    """), encoding="utf-8")

    return base


@pytest.fixture(scope="module")
def heuristic_result(smelly_project_dir) -> HeuristicResult:
    """Строит ProjectMap и запускает identify_candidates по smelly_project_dir."""
    py_files = [
        str(smelly_project_dir / "smells.py"),
        str(smelly_project_dir / "clean.py"),
    ]
    pm = build_project_map(py_files)
    return identify_candidates(pm)


# ---------------------------------------------------------------------------
# Тест 1: все ожидаемые rule-коды присутствуют в findings
# ---------------------------------------------------------------------------

class TestAllRulesPresent:
    # OCP-H-002 (match/case) покрывается отдельным файлом.
    EXPECTED_RULES = {
        "LSP-H-001",
        "LSP-H-002",
        "LSP-H-004",
        "OCP-H-001",
        "OCP-H-004",
    }

    def test_all_expected_rules_present(self, heuristic_result):
        """Каждый ожидаемый rule-код должен встретиться хотя бы в одном finding."""
        found_rules = {f.rule for f in heuristic_result.findings}
        missing = self.EXPECTED_RULES - found_rules
        assert not missing, f"Missing rule codes in findings: {missing}"

    def test_no_unexpected_rule_codes(self, heuristic_result):
        """В findings не должно появляться неизвестных rule-кодов."""
        known_rules = {
            "LSP-H-001", "LSP-H-002", "LSP-H-004",
            "OCP-H-001", "OCP-H-002", "OCP-H-004",
        }
        found_rules = {f.rule for f in heuristic_result.findings}
        unknown = found_rules - known_rules
        assert not unknown, f"Unknown rule codes appeared: {unknown}"


# ---------------------------------------------------------------------------
# Тест 2: отсутствие ложных срабатываний на чистом коде
# ---------------------------------------------------------------------------

class TestNoFalsePositivesOnCleanCode:

    def test_clean_classes_produce_no_findings(self, smelly_project_dir):
        """Классы из clean.py не должны давать ни одного finding."""
        pm = build_project_map([str(smelly_project_dir / "clean.py")])
        result = identify_candidates(pm)
        assert result.findings == [], (
            f"Expected no findings for clean code, got: "
            f"{[f.rule for f in result.findings]}"
        )

    def test_clean_classes_not_in_candidates_as_findings(self, smelly_project_dir):
        """Чистые классы не должны попадать в кандидаты по причинам эвристик."""
        pm = build_project_map([str(smelly_project_dir / "clean.py")])
        result = identify_candidates(pm)
        for candidate in result.candidates:
            assert candidate.heuristic_reasons == [], (
                f"Clean class '{candidate.class_name}' unexpectedly has "
                f"heuristic reasons: {candidate.heuristic_reasons}"
            )


# ---------------------------------------------------------------------------
# Тест 3: корректность метаданных findings
# ---------------------------------------------------------------------------

class TestFindingMetadataIntegrity:

    def test_all_findings_have_required_fields(self, heuristic_result):
        """Каждый finding должен иметь все обязательные поля."""
        for finding in heuristic_result.findings:
            assert finding.rule, f"Empty rule in finding: {finding}"
            assert finding.file, f"Empty file in finding: {finding}"
            assert finding.message, f"Empty message in finding: {finding}"
            assert finding.source == "heuristic", f"Wrong source: {finding.source}"
            assert finding.severity in ("warning", "info"), f"Wrong severity: {finding.severity}"
            assert finding.details is not None, f"Missing details in: {finding}"

    def test_lsp_rules_have_lsp_principle(self, heuristic_result):
        """Все findings с rule=LSP-H-* должны иметь details.principle == 'LSP'."""
        lsp_findings = [f for f in heuristic_result.findings if f.rule.startswith("LSP")]
        assert lsp_findings, "Expected at least one LSP finding"
        for finding in lsp_findings:
            assert finding.details is not None
            assert finding.details.principle == "LSP", (
                f"Rule {finding.rule} has wrong principle: {finding.details.principle}"
            )

    def test_ocp_rules_have_ocp_principle(self, heuristic_result):
        """Все findings с rule=OCP-H-* должны иметь details.principle == 'OCP'."""
        ocp_findings = [f for f in heuristic_result.findings if f.rule.startswith("OCP")]
        assert ocp_findings, "Expected at least one OCP finding"
        for finding in ocp_findings:
            assert finding.details is not None
            assert finding.details.principle == "OCP", (
                f"Rule {finding.rule} has wrong principle: {finding.details.principle}"
            )


# ---------------------------------------------------------------------------
# Тест 4: корректность кандидатов (candidates)
# ---------------------------------------------------------------------------

class TestCandidatesIntegrity:

    def test_smelly_classes_are_candidates(self, heuristic_result):
        """Все smell-классы из интеграционного sample должны попасть в candidates."""
        expected_candidates = {
            "XmlSerializer",
            "SilentNotifier",
            "BrokenService",
            "ShapeRenderer",
            "ComplexProcessor",
            "HighPrioritySmell",
        }
        candidate_names = {c.class_name for c in heuristic_result.candidates}
        missing = expected_candidates - candidate_names
        assert not missing, f"Expected classes not in candidates: {missing}"

    def test_high_priority_class_ranked_first(self, heuristic_result):
        """Класс с несколькими нарушениями должен быть среди верхних кандидатов."""
        priorities = [c.priority for c in heuristic_result.candidates]
        assert priorities == sorted(priorities, reverse=True), (
            "Candidates are not sorted by priority in descending order"
        )
        top_names = [c.class_name for c in heuristic_result.candidates[:3]]
        assert "HighPrioritySmell" in top_names, (
            f"HighPrioritySmell not in top-3 candidates: {top_names}"
        )

    def test_candidates_have_valid_candidate_type(self, heuristic_result):
        """Каждый кандидат должен иметь валидный агрегированный тип."""
        valid_types = {"ocp", "lsp", "both"}
        for candidate in heuristic_result.candidates:
            assert candidate.candidate_type in valid_types


# ---------------------------------------------------------------------------
# Тест 5: диагностика ProjectMap от парсера
# ---------------------------------------------------------------------------

class TestIntegrationProjectMapDiagnostics:

    def test_lsp_sample_classes_have_expected_parser_metadata(self, smelly_project_dir):
        """Интеграционный sample строит корректный ProjectMap."""
        pm = build_project_map([
            str(smelly_project_dir / "smells.py"),
            str(smelly_project_dir / "clean.py"),
        ])

        assert "XmlSerializer" in pm.classes
        assert "SilentNotifier" in pm.classes

        xml_info = pm.classes["XmlSerializer"]
        notifier_info = pm.classes["SilentNotifier"]

        assert "BaseSerializer" in xml_info.parent_classes
        assert "BaseNotifier" in notifier_info.parent_classes

        xml_methods = {m.name: m for m in xml_info.methods}
        notifier_methods = {m.name: m for m in notifier_info.methods}

        assert "serialize" in xml_methods
        assert "notify" in notifier_methods

        assert xml_methods["serialize"].is_override is True, (
            "XmlSerializer.serialize должен быть override для интеграционного LSP-H-001"
        )
        assert notifier_methods["notify"].is_override is True, (
            "SilentNotifier.notify должен быть override для интеграционного LSP-H-002"
        )


# ---------------------------------------------------------------------------
# Тест 6: диагностика полного LSP-пути
# ---------------------------------------------------------------------------

class TestLspPipelineDiagnostics:

    def test_xml_and_silent_notifier_survive_full_lsp_pipeline(self, smelly_project_dir):
        """
        Диагностика полного LSP-пути:
        1) классы есть в ProjectMap
        2) raw-check функции дают findings
        3) findings не теряются до финального HeuristicResult
        """
        pm = build_project_map([
            str(smelly_project_dir / "smells.py"),
            str(smelly_project_dir / "clean.py"),
        ])

        assert "XmlSerializer" in pm.classes
        assert "SilentNotifier" in pm.classes

        xml_info = pm.classes["XmlSerializer"]
        silent_info = pm.classes["SilentNotifier"]

        xml_node = ast.parse(xml_info.source_code).body[0]
        silent_node = ast.parse(silent_info.source_code).body[0]

        assert isinstance(xml_node, ast.ClassDef)
        assert isinstance(silent_node, ast.ClassDef)

        # Сырые findings отдельных эвристик
        xml_findings = lsp_h_001.check(xml_node, xml_info, pm)
        silent_findings = lsp_h_002.check(silent_node, silent_info, pm)

        assert xml_findings, "XmlSerializer не дал raw finding для LSP-H-001"
        assert silent_findings, "SilentNotifier не дал raw finding для LSP-H-002"

        # Дедупликация не должна убивать findings
        merged = _deduplicate_findings(xml_findings + silent_findings)
        merged_keys = {(f.class_name, f.rule) for f in merged}

        assert ("XmlSerializer", "LSP-H-001") in merged_keys
        assert ("SilentNotifier", "LSP-H-002") in merged_keys

        # Финальный результат пайплайна тоже обязан их содержать
        result = identify_candidates(pm)
        result_rules = {(f.class_name, f.rule) for f in result.findings}
        candidate_names = {c.class_name for c in result.candidates}

        assert ("XmlSerializer", "LSP-H-001") in result_rules
        assert ("SilentNotifier", "LSP-H-002") in result_rules
        assert "XmlSerializer" in candidate_names
        assert "SilentNotifier" in candidate_names
