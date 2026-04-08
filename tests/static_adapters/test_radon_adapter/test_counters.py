# test_counters.py — тесты агрегированных метрик RadonAdapter.run().
# Проверяет mean_cc, high_complexity_count, сортировку items, инвариант total_items.
# Изоляция: subprocess.run патчится во всех тестах, LIZARD_AVAILABLE=False.
from unittest.mock import patch

from tests.static_adapters.test_radon_adapter.helpers import assert_success_schema

_PATCH_SUBPROCESS = "solid_dashboard.adapters.radon_adapter.subprocess.run"
_PATCH_LIZARD = "solid_dashboard.adapters.radon_adapter.LIZARD_AVAILABLE"


def _make_block(name: str, complexity: int, lineno: int = 1, btype: str = "function") -> dict:
    # фабрика минимального блока для сборки JSON-ввода
    return {"name": name, "type": btype, "complexity": complexity, "rank": "A", "lineno": lineno}


def _run_with_output(adapter, tmp_py_project, base_config, radon_json_str: str) -> dict:
    # патчит subprocess.run и lizard, возвращает результат run()
    mock_proc = type("CP", (), {"stdout": radon_json_str, "returncode": 0})()
    with patch(_PATCH_SUBPROCESS, return_value=mock_proc), \
         patch(_PATCH_LIZARD, False):
        return adapter.run(
            target_dir=str(tmp_py_project),
            context={},
            config=base_config,
        )


class TestMeanCc:
    """mean_cc: значение при items=[], округление, float-тип."""

    def test_mean_cc_zero_when_no_items(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # пустой JSON — mean_cc должен быть 0.0, а не ZeroDivisionError
        result = _run_with_output(
            adapter, tmp_py_project, base_config, make_radon_output([])
        )
        assert result["mean_cc"] == 0.0

    def test_mean_cc_is_float_type(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # mean_cc должен быть float даже если значение целочисленное (round возвращает float)
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [_make_block("f", complexity=6)],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert isinstance(result["mean_cc"], float)

    def test_mean_cc_single_item(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # единственный item с cc=6 — mean_cc=6.0
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [_make_block("f", complexity=6)],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["mean_cc"] == 6.0

    def test_mean_cc_rounds_to_two_decimals(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # cc=[1, 1, 2] — sum=4, total=3, mean=1.3333... — round(до 2)=1.33
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [
                _make_block("f1", complexity=1, lineno=1),
                _make_block("f2", complexity=1, lineno=2),
                _make_block("f3", complexity=2, lineno=3),
            ],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["mean_cc"] == round(4 / 3, 2)

    def test_mean_cc_exact_integer_sum(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # cc=[3, 5, 7] — sum=15, total=3, mean=5.0
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [
                _make_block("f1", complexity=3, lineno=1),
                _make_block("f2", complexity=5, lineno=2),
                _make_block("f3", complexity=7, lineno=3),
            ],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["mean_cc"] == 5.0


class TestHighComplexityCount:
    """high_complexity_count: порог > 10 строго."""

    def test_complexity_10_not_counted(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # значение 10 ровно порогу — не превышает, не считается
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [_make_block("boundary", complexity=10)],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["high_complexity_count"] == 0

    def test_complexity_11_counted(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # значение 11 превышает порог — считается
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [_make_block("over", complexity=11)],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["high_complexity_count"] == 1

    def test_all_under_threshold_gives_zero(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # все cc ≤ 10 — счетчик 0
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [
                _make_block("f1", complexity=5, lineno=1),
                _make_block("f2", complexity=8, lineno=2),
                _make_block("f3", complexity=10, lineno=3),
            ],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["high_complexity_count"] == 0

    def test_multiple_high_complexity_items(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # несколько блоков с cc > 10 — счетчик соответствует их количеству
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [
                _make_block("f1", complexity=5,  lineno=1),
                _make_block("f2", complexity=12, lineno=2),
                _make_block("f3", complexity=15, lineno=3),
                _make_block("f4", complexity=3,  lineno=4),
                _make_block("f5", complexity=11, lineno=5),
            ],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["high_complexity_count"] == 3


class TestSortingAndTotalItems:
    """items сортируются по complexity descending, total_items == len(items)."""

    def test_items_sorted_by_complexity_descending(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # подаем блоки в произвольном порядке, ожидаем сортировку по убыванию
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [
                _make_block("low",  complexity=2,  lineno=1),
                _make_block("high", complexity=15, lineno=2),
                _make_block("mid",  complexity=7,  lineno=3),
            ],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        complexities = [item["complexity"] for item in result["items"]]
        assert complexities == sorted(complexities, reverse=True)

    def test_total_items_matches_filtered_count(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # total_items должен соответствовать len(items): 3 function + 1 class = 3 проходящих
        radon_json = make_radon_output([{
            "filepath": "app/a.py",
            "blocks": [
                _make_block("f1", complexity=2, lineno=1),
                _make_block("f2", complexity=4, lineno=2),
                _make_block("f3", complexity=6, lineno=3),
                _make_block("cls", complexity=1, lineno=4, btype="class"),
            ],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["total_items"] == 3
        assert result["total_items"] == len(result["items"])

    def test_items_across_multiple_files_sorted_globally(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # блоки из разных файлов сортируются глобально по cc descending
        radon_json = make_radon_output([
            {
                "filepath": "app/a.py",
                "blocks": [_make_block("fa", complexity=3, lineno=1)],
            },
            {
                "filepath": "app/b.py",
                "blocks": [_make_block("fb", complexity=20, lineno=1)],
            },
            {
                "filepath": "app/c.py",
                "blocks": [_make_block("fc", complexity=8, lineno=1)],
            },
        ])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        complexities = [item["complexity"] for item in result["items"]]
        assert complexities == sorted(complexities, reverse=True)
        assert complexities[0] == 20
