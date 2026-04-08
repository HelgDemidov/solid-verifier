# test_run_integration.py — интеграционные тесты полного пайплайна adapter.run().
#
# Стратегия: subprocess.run подменяется через patch, но весь внутренний код
# адаптера (парсер, детектор, confidence, деduplication, abort-guard) проходит
# в реальном режиме — без дополнительных моков.
#
# Граница ответственности:
#   - Этот файл: контракт run() как единого публичного метода.
#   - test_parser.py:      детали парсинга raw_output → nodes/edges.
#   - test_confidence.py:  логика маркировки confidence.
#   - test_deduplication.py: схлопывание дублей.
#   - test_detect_suspicious_blocks.py: детектор collision.
#   - test_error_paths.py: все _error()-пути.
#
# Примечание о pytest.warns:
# Тесты групп 3 и 4, намеренно создающие suspicious-блоки, используют
# pytest.warns(RuntimeWarning, match="high collision rate") — Стратегия A.
# Это декларирует RuntimeWarning как часть контракта: если адаптер перестанет
# эмитировать предупреждение, тест упадёт и регрессия будет поймана.
import pytest
from unittest.mock import patch, MagicMock

from tests.static_adapters.test_pyan3_adapter.helpers import (
    make_raw_output,
    assert_success_schema,
    assert_error_schema,
)


# Вспомогательная функция: запускает adapter.run() с подменённым subprocess.run
def _run_with_output(
    adapter,
    tmp_py_project,
    config,
    raw_output: str,
    returncode: int = 0,
    stderr: str = "",
):
    mock_result = MagicMock()
    mock_result.returncode = returncode
    mock_result.stdout = raw_output
    mock_result.stderr = stderr
    with patch("solid_dashboard.adapters.pyan3_adapter.subprocess.run", return_value=mock_result):
        return adapter.run(str(tmp_py_project), {}, config)


# ---------------------------------------------------------------------------
# Группа 1: Success schema — структурные инварианты успешного ответа
# ---------------------------------------------------------------------------

class TestSuccessSchema:

    def test_successful_run_returns_full_schema(self, adapter, tmp_py_project, base_config):
        # базовый прогон с корректным raw_output возвращает полный success-schema
        raw = make_raw_output([("A", "B"), ("B", "C")])
        result = _run_with_output(adapter, tmp_py_project, base_config, raw)
        assert_success_schema(result)

    def test_collision_rate_is_present_and_normalized(self, adapter, tmp_py_project, base_config):
        # collision_rate должен присутствовать в ответе и быть в диапазоне [0.0, 1.0]
        raw = make_raw_output([("A", "B"), ("B", "C")])
        result = _run_with_output(adapter, tmp_py_project, base_config, raw)
        assert_success_schema(result)
        assert "collision_rate" in result
        assert 0.0 <= result["collision_rate"] <= 1.0

    def test_raw_output_preserved_in_result(self, adapter, tmp_py_project, base_config):
        # raw_output прокидывается в результат без изменений
        raw = make_raw_output([("SomeService", "OtherRepo")])
        result = _run_with_output(adapter, tmp_py_project, base_config, raw)
        assert_success_schema(result)
        assert result["raw_output"] == raw


# ---------------------------------------------------------------------------
# Группа 2: Counts invariants — числовые инварианты между полями результата
# ---------------------------------------------------------------------------

class TestNodeAndEdgeCounts:

    def test_node_count_equals_len_nodes(self, adapter, tmp_py_project, base_config):
        # node_count всегда соответствует длине списка nodes
        raw = make_raw_output([("A", "B"), ("A", "C"), ("B", "D")])
        result = _run_with_output(adapter, tmp_py_project, base_config, raw)
        assert_success_schema(result)
        assert result["node_count"] == len(result["nodes"])

    def test_edge_count_equals_high_plus_low(self, adapter, tmp_py_project, base_config):
        # глобальный инвариант: edge_count == edge_count_high + edge_count_low
        raw = make_raw_output([("A", "B"), ("B", "C"), ("C", "D")])
        result = _run_with_output(adapter, tmp_py_project, base_config, raw)
        assert_success_schema(result)
        assert result["edge_count"] == result["edge_count_high"] + result["edge_count_low"]

    def test_dead_plus_root_node_count_does_not_exceed_node_count(self, adapter, tmp_py_project, base_config):
        # dead_nodes и root_nodes — непересекающиеся подмножества nodes
        raw = make_raw_output([("Root", "Middle"), ("Middle", "Leaf")])
        result = _run_with_output(adapter, tmp_py_project, base_config, raw)
        assert_success_schema(result)
        assert result["dead_node_count"] + result["root_node_count"] <= result["node_count"]
        # дополнительно: dead и root не пересекаются
        assert not set(result["dead_nodes"]) & set(result["root_nodes"])


# ---------------------------------------------------------------------------
# Группа 3: Suspicious blocks — поле suspicious_blocks в результате
# ---------------------------------------------------------------------------

class TestSuspiciousBlocksInResult:

    def test_no_suspicious_blocks_when_output_is_clean(self, adapter, tmp_py_project, base_config):
        # чистый raw_output без коллизий → suspicious_blocks пуст
        raw = make_raw_output([("A", "B"), ("B", "C")])
        result = _run_with_output(adapter, tmp_py_project, base_config, raw)
        assert_success_schema(result)
        assert result["suspicious_blocks"] == []
        assert result["collision_rate"] == 0.0

    def test_suspicious_block_appears_in_result(self, adapter, tmp_py_project, base_config):
        # блок с name collision попадает в suspicious_blocks результата
        #
        # Ожидаемый side-effect: 1 suspicious из 2 узлов = 50% > порога 35%
        # → RuntimeWarning о высоком collision rate (Стратегия A).
        raw = make_raw_output([("login", "handler")], extra_used={"login": ["handler"]})
        with pytest.warns(RuntimeWarning, match="high collision rate"):
            result = _run_with_output(adapter, tmp_py_project, base_config, raw)
        assert_success_schema(result)
        assert "login" in result["suspicious_blocks"]
        assert result["collision_rate"] > 0.0


# ---------------------------------------------------------------------------
# Группа 4: AbortOnHighCollision — поведение при abort_on_high_collision=True/False
# ---------------------------------------------------------------------------

class TestAbortOnHighCollision:

    def test_high_collision_without_abort_returns_success(self, adapter, tmp_py_project, base_config):
        # abort_on_high_collision=False (дефолт): высокий collision_rate
        # эмитирует RuntimeWarning, но пайплайн продолжает работу и возвращает
        # is_success=True с помеченными low-confidence рёбрами
        #
        # Это единственный способ убедиться, что abort=False не прерывает пайплайн.
        raw = make_raw_output([("login", "handler")], extra_used={"login": ["handler"]})
        no_abort_config = {
            **base_config,
            "pyan3": {
                "abort_on_high_collision": False,
                "collision_rate_threshold": 0.35,
            },
        }
        with pytest.warns(RuntimeWarning, match="high collision rate"):
            result = _run_with_output(adapter, tmp_py_project, no_abort_config, raw)
        assert_success_schema(result)
        assert result["collision_rate"] > 0.35
        # рёбра из suspicious-блоков помечены low, но результат не прерван
        assert result["edge_count_low"] > 0

    def test_abort_on_high_collision_returns_error_after_full_parse(self, adapter, tmp_py_project, base_config):
        # abort_on_high_collision=True: пайплайн проходит весь путь
        # (парсинг, детектор, вычисление collision_rate) и только ПОТОМ
        # возвращает _error() — это интеграционный тест, который не мог быть
        # покрыт в test_error_paths.py без полного прогона run().
        #
        # Последовательность:
        #   subprocess mock → парсер → _detect_suspicious_blocks → collision_rate > threshold
        #   → RuntimeWarning (Стратегия A) → abort → _error("Aborted: collision_rate...")
        raw = make_raw_output([("login", "handler")], extra_used={"login": ["handler"]})
        abort_config = {
            **base_config,
            "pyan3": {
                "abort_on_high_collision": True,
                "collision_rate_threshold": 0.35,
            },
        }
        with pytest.warns(RuntimeWarning, match="high collision rate"):
            result = _run_with_output(adapter, tmp_py_project, abort_config, raw)
        assert_error_schema(result)
        assert "Aborted" in result["error"]
        assert "collision_rate" in result["error"]
        # raw_output прокидывается даже в error-ответе — адаптер не теряет данные
        assert result["raw_output"] == raw
