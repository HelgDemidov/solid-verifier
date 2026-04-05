"""
Интеграционные тесты изолированных механизмов:
  - TestHeuristicsDedupIntegration: дедупликация findings и candidates
    при одновременном срабатывании OCP-H-001 и OCP-H-004 на одном методе
  - TestOcpH002Integration: эвристика OCP-H-002 (match/case, Python 3.10+)

Оба класса используют локальные tmp_path-фикстуры и независимы
от smelly_project_dir / heuristic_result из test_integration_pipeline.py.

Запуск:
  pytest tools/solid_verifier/tests/llm/test_heuristics/test_integration_dedup_and_ocp_h002.py -v
"""
import sys
import textwrap
from pathlib import Path

import pytest

from solid_dashboard.llm.ast_parser import build_project_map
from solid_dashboard.llm.heuristics import identify_candidates

pytestmark = pytest.mark.integration


# ===========================================================================
# Тест дедупликации (findings + candidates)
# ===========================================================================

class TestHeuristicsDedupIntegration:
    """
    На одном и том же методе срабатывают OCP-H-001 и OCP-H-004.
    Проверяем, что findings дедуплицированы: OCP-H-001 побеждает,
    OCP-H-004 поглощается и отражается в explanation OCP-H-001.
    """

    def test_findings_and_candidates_are_deduplicated(self, tmp_path: Path):
        source = """
        class Circle: pass
        class Square: pass
        class Triangle: pass
        class Hexagon: pass
        class Pentagon: pass

        class ShapeRenderer:
            def render(self, shape, value):
                if isinstance(shape, Circle): pass
                elif isinstance(shape, Square): pass
                elif isinstance(shape, Triangle): pass
                elif isinstance(shape, Hexagon): pass
                elif isinstance(shape, Pentagon): pass
                else: pass

                if value > 10: value += 1
                if value < -10: value -= 1
                if value == 0: value = 42
                if value % 2 == 0: value *= 2
                if value == 84: value //= 2

                return value
        """
        module_path = tmp_path / "shapes_module.py"
        module_path.write_text(textwrap.dedent(source), encoding="utf-8")

        pm = build_project_map([module_path])
        # exclude_patterns=[] — отключаем фильтрацию путей, т.к. pytest
        # генерирует tmp_path содержащий "test_" и файл иначе игнорируется
        result = identify_candidates(pm, exclude_patterns=[])

        # OCP-H-001 должен быть ровно один
        ocp_h001 = [f for f in result.findings if f.rule == "OCP-H-001"]
        assert len(ocp_h001) == 1

        # OCP-H-001 должен поглотить OCP-H-004 и отразить это в explanation
        winner = ocp_h001[0]
        assert winner.details is not None
        assert "Also detected: OCP-H-004" in (winner.details.explanation or "")

        # OCP-H-004 должен быть подавлен
        ocp_h004 = [f for f in result.findings if f.rule == "OCP-H-004"]
        assert ocp_h004 == []

        # ShapeRenderer должен быть ровно одним кандидатом
        shape_candidates = [c for c in result.candidates if c.class_name == "ShapeRenderer"]
        assert len(shape_candidates) == 1

        # Кандидат должен нести причины обеих эвристик
        candidate = shape_candidates[0]
        assert "OCP-H-001" in candidate.heuristic_reasons
        assert "OCP-H-004" in candidate.heuristic_reasons


# ===========================================================================
# Тест OCP-H-002 (match/case)
# ===========================================================================

@pytest.mark.skipif(
    sys.version_info < (3, 10),
    reason="match/case syntax requires Python 3.10+"
)
class TestOcpH002Integration:

    @pytest.fixture
    def match_project_dir(self, tmp_path_factory):
        """Временная директория с dispatcher.py — match/case с 3+ ветвями."""
        base = tmp_path_factory.mktemp("match_project")
        (base / "dispatcher.py").write_text(textwrap.dedent("""
        class Created: pass
        class Updated: pass
        class Deleted: pass

        class EventDispatcher:
            def dispatch(self, event):
                match event:
                    case Created(): pass
                    case Updated(): pass
                    case Deleted(): pass

        class TwoBranchDispatcher:
            def route(self, cmd):
                match cmd:
                    case Created(): pass
                    case Updated(): pass
        """), encoding="utf-8")
        return base

    def test_ocp_h002_finding_present(self, match_project_dir):
        """EventDispatcher с 3+ match-ветвями должен давать finding OCP-H-002."""
        pm = build_project_map([match_project_dir])
        result = identify_candidates(pm)
        rules = [f.rule for f in result.findings]
        assert "OCP-H-002" in rules

    def test_ocp_h002_candidate_registered(self, match_project_dir):
        """EventDispatcher должен попасть в candidates."""
        pm = build_project_map([match_project_dir])
        result = identify_candidates(pm)
        candidate_names = [c.class_name for c in result.candidates]
        assert "EventDispatcher" in candidate_names
