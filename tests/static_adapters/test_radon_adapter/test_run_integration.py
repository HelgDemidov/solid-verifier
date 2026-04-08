# test_run_integration.py — интеграционные тесты полного контракта run().
#
# Фокус: не отдельные фичи, а системные инварианты всего вывода целиком:
#   — schema-полнота (все ключи + поля каждого item)
#   — математическая связь между полями (total_items, mean_cc, high_complexity_count)
#   — поведение на граничных входах (пустой JSON, многофайловый ввод)
#
# Стратегия: lizard отключен (None/False) во всех тестах —
# интеграция тестирует только radon-пайплайн, не пересекаясь с test_lizard_enrichment.
import json
from unittest.mock import patch

from tests.static_adapters.test_radon_adapter.helpers import (
    assert_success_schema,
    assert_item_fields,
)

_PATCH_SUBPROCESS = "solid_dashboard.adapters.radon_adapter.subprocess.run"
_PATCH_LIZARD_MOD = "solid_dashboard.adapters.radon_adapter.lizard"
_PATCH_LIZARD_FLAG = "solid_dashboard.adapters.radon_adapter.LIZARD_AVAILABLE"


def _proc(stdout: str):
    # минимальный CompletedProcess-мок: returncode=0, stdout=stdout
    from unittest.mock import MagicMock
    m = MagicMock()
    m.stdout = stdout
    m.returncode = 0
    return m


def _run(adapter, tmp_py_project, radon_json: str, config: dict = None) -> dict:
    # вспомогатель: патчим subprocess + lizard и запускаем adapter.run()
    cfg = config if config is not None else {"ignore_dirs": []}
    with patch(_PATCH_SUBPROCESS, return_value=_proc(radon_json)), \
         patch(_PATCH_LIZARD_MOD, None), \
         patch(_PATCH_LIZARD_FLAG, False):
        return adapter.run(
            target_dir=str(tmp_py_project), context={}, config=cfg
        )


# предопределенные JSON-фикстуры для шаринга между тестами
_TWO_FUNCS = json.dumps({"app/module.py": [
    {"name": "fast",  "type": "function", "complexity": 3,  "rank": "A", "lineno": 10},
    {"name": "heavy", "type": "function", "complexity": 15, "rank": "C", "lineno": 40},
]})

_THREE_MIXED = json.dumps({"app/module.py": [
    {"name": "MyClass",  "type": "class",    "complexity": 2,  "rank": "A", "lineno": 1},
    {"name": "do_work",  "type": "function", "complexity": 4,  "rank": "A", "lineno": 20},
    {"name": "dispatch", "type": "method",   "complexity": 12, "rank": "B", "lineno": 50},
]})


class TestAdapterIdentity:
    """adapter.name — часть публичного контракта."""

    def test_adapter_name_is_radon(self, adapter):
        # adapter.name семантически фиксирован: понадобится pipeline для роутинга
        assert adapter.name == "radon"


class TestFullSchemaAndItemFields:
    """Схема-целостность: все ключи run() + все поля каждого item."""

    def test_success_schema_all_keys_present(
        self, adapter, tmp_py_project
    ):
        # полный schema-ассерт: все 5 ключей + без "error"
        result = _run(adapter, tmp_py_project, _TWO_FUNCS)
        assert_success_schema(result)

    def test_each_item_has_required_fields(
        self, adapter, tmp_py_project
    ):
        # каждый item в items[] должен содержать: name, type, complexity, rank, lineno, filepath
        result = _run(adapter, tmp_py_project, _TWO_FUNCS)
        for item in result["items"]:
            assert_item_fields(item)


class TestNumericalInvariants:
    """Числовые связи между полями вывода."""

    def test_total_items_equals_len_items(
        self, adapter, tmp_py_project
    ):
        # total_items точно отражает len(items) — не предвычисленное значение
        result = _run(adapter, tmp_py_project, _TWO_FUNCS)
        assert result["total_items"] == len(result["items"])

    def test_mean_cc_matches_manual_calculation(
        self, adapter, tmp_py_project
    ):
        # mean_cc == round(sum(item.complexity) / n, 2) — математическая связь
        # _TWO_FUNCS: cc=[3,15], sum=18, n=2 -> mean_cc=9.0
        result = _run(adapter, tmp_py_project, _TWO_FUNCS)
        items = result["items"]
        expected = round(sum(i["complexity"] for i in items) / len(items), 2)
        assert result["mean_cc"] == expected

    def test_high_complexity_count_lte_total_items(
        self, adapter, tmp_py_project
    ):
        # high_complexity_count <= total_items — инвариант (не больше чем всего)
        result = _run(adapter, tmp_py_project, _TWO_FUNCS)
        assert result["high_complexity_count"] <= result["total_items"]

    def test_high_complexity_count_matches_threshold(
        self, adapter, tmp_py_project
    ):
        # _TWO_FUNCS: cc=[3,15] — только heavy(15>10) считается, итого: 1
        result = _run(adapter, tmp_py_project, _TWO_FUNCS)
        assert result["high_complexity_count"] == 1


class TestEdgeCasesIntegration:
    """Крайние входы: пустой JSON, class-фильтрация, многофайловый ввод."""

    def test_empty_json_returns_zero_counts(
        self, adapter, tmp_py_project
    ):
        # пустой JSON — не ошибка, а нулевые счетчики и пустой items
        result = _run(adapter, tmp_py_project, json.dumps({}))
        assert_success_schema(result)
        assert result["total_items"] == 0
        assert result["mean_cc"] == 0.0
        assert result["high_complexity_count"] == 0
        assert result["items"] == []

    def test_class_blocks_excluded_from_items(
        self, adapter, tmp_py_project
    ):
        # _THREE_MIXED: class=1, function=1, method=1 — class фильтруется,
        # в items остаются только function + method
        result = _run(adapter, tmp_py_project, _THREE_MIXED)
        types_in_items = {item["type"] for item in result["items"]}
        assert "class" not in types_in_items
        assert result["total_items"] == 2

    def test_multifile_json_aggregates_all_items(
        self, adapter, tmp_py_project
    ):
        # три файла по 2 блока каждый — total_items==6, сортировка desc по complexity
        multifile = json.dumps({
            "a.py": [
                {"name": "f1", "type": "function", "complexity": 5,  "rank": "A", "lineno": 1},
                {"name": "f2", "type": "function", "complexity": 11, "rank": "B", "lineno": 10},
            ],
            "b.py": [
                {"name": "f3", "type": "function", "complexity": 2,  "rank": "A", "lineno": 1},
                {"name": "f4", "type": "method",   "complexity": 14, "rank": "C", "lineno": 20},
            ],
            "c.py": [
                {"name": "f5", "type": "function", "complexity": 7,  "rank": "A", "lineno": 1},
                {"name": "f6", "type": "function", "complexity": 1,  "rank": "A", "lineno": 30},
            ],
        })
        result = _run(adapter, tmp_py_project, multifile)
        assert_success_schema(result)
        assert result["total_items"] == 6

        # сортировка descending по complexity — инвариант вывода
        complexities = [i["complexity"] for i in result["items"]]
        assert complexities == sorted(complexities, reverse=True)

        # high: cc=[5,11,2,14,7,1], >10 → [11,14] — итого 2
        assert result["high_complexity_count"] == 2
