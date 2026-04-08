# test_parser.py — тесты JSON-парсера RadonAdapter.run().
# Проверяет фильтрацию блоков по type, пропуск str-значений,
# дефолты полей, прокидывание filepath.
# Изоляция: subprocess.run патчится во всех тестах, lizard отключён патчем LIZARD_AVAILABLE=False.
from unittest.mock import patch
import pytest

from tests.static_adapters.test_radon_adapter.helpers import (
    assert_success_schema,
    assert_item_fields,
)

_PATCH_SUBPROCESS = "solid_dashboard.adapters.radon_adapter.subprocess.run"
_PATCH_LIZARD = "solid_dashboard.adapters.radon_adapter.LIZARD_AVAILABLE"


def _run_with_output(adapter, tmp_py_project, base_config, radon_json_str: str) -> dict:
    # вспомогательная функция: патчит subprocess.run и lizard, возвращает результат run()
    mock_proc = type("CP", (), {"stdout": radon_json_str, "returncode": 0})()
    with patch(_PATCH_SUBPROCESS, return_value=mock_proc), \
         patch(_PATCH_LIZARD, False):
        return adapter.run(
            target_dir=str(tmp_py_project),
            context={},
            config=base_config,
        )


class TestEmptyAndMinimal:
    """JSON без блоков и минимальные сценарии."""

    def test_empty_json_object_gives_zero_items(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # пустой JSON-объект: нет файлов, нет блоков
        result = _run_with_output(
            adapter, tmp_py_project, base_config, make_radon_output([])
        )
        assert_success_schema(result)
        assert result["total_items"] == 0
        assert result["items"] == []

    def test_single_function_block_parsed(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # единственный блок type="function" должен попасть в items
        radon_json = make_radon_output([{
            "filepath": "app/module.py",
            "blocks": [{
                "name": "my_func", "type": "function",
                "complexity": 3, "rank": "A", "lineno": 5,
            }],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert_success_schema(result)
        assert result["total_items"] == 1
        assert result["items"][0]["name"] == "my_func"

    def test_single_method_block_parsed(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # блок type="method" тоже проходит фильтрацию
        radon_json = make_radon_output([{
            "filepath": "app/service.py",
            "blocks": [{
                "name": "handle", "type": "method",
                "complexity": 2, "rank": "A", "lineno": 12,
            }],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["total_items"] == 1
        assert result["items"][0]["type"] == "method"


class TestTypeFiltering:
    """type=class и прочие не (function|method) должны фильтроваться."""

    def test_class_block_filtered_out(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # блок type="class" не должен попадать в items
        radon_json = make_radon_output([{
            "filepath": "app/models.py",
            "blocks": [{
                "name": "MyModel", "type": "class",
                "complexity": 1, "rank": "A", "lineno": 1,
            }],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["total_items"] == 0
        assert result["items"] == []

    def test_multiple_types_only_function_method_kept(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # смешанный ввод: function + method + class — проходят только первые два
        radon_json = make_radon_output([{
            "filepath": "app/views.py",
            "blocks": [
                {"name": "get", "type": "function", "complexity": 4, "rank": "A", "lineno": 10},
                {"name": "post", "type": "method", "complexity": 5, "rank": "A", "lineno": 20},
                {"name": "MyView", "type": "class", "complexity": 1, "rank": "A", "lineno": 5},
            ],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["total_items"] == 2
        names = {item["name"] for item in result["items"]}
        assert names == {"get", "post"}

    def test_unknown_type_filtered_out(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # неизвестный type (например "lambda") тоже фильтруется
        radon_json = make_radon_output([{
            "filepath": "app/utils.py",
            "blocks": [{
                "name": "helper", "type": "lambda",
                "complexity": 1, "rank": "A", "lineno": 3,
            }],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["total_items"] == 0


class TestStrBlockSkip:
    """str-значения вместо списка (radon-ответ на невалидный файл) пропускаются."""

    def test_str_block_value_skipped(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # radon возвращает строку вместо списка для синтаксически невалидного файла
        radon_json = make_radon_output([{
            "filepath": "app/broken.py",
            "blocks": "SyntaxError: invalid syntax (<unknown>, line 5)",
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert_success_schema(result)
        assert result["total_items"] == 0
        assert result["items"] == []

    def test_mixed_str_and_list_only_list_processed(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # один filepath — строка, другой — список: обрабатывается только второй
        radon_json = make_radon_output([
            {
                "filepath": "app/broken.py",
                "blocks": "SyntaxError: bad syntax",
            },
            {
                "filepath": "app/good.py",
                "blocks": [{
                    "name": "ok_func", "type": "function",
                    "complexity": 2, "rank": "A", "lineno": 1,
                }],
            },
        ])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["total_items"] == 1
        assert result["items"][0]["name"] == "ok_func"


class TestItemFields:
    """filepath прокидывается, дефолты полей, полнота assert_item_fields."""

    def test_filepath_preserved_in_item(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # filepath из JSON-ключа должен прокидываться в item["filepath"]
        radon_json = make_radon_output([{
            "filepath": "app/services/payment.py",
            "blocks": [{
                "name": "process", "type": "function",
                "complexity": 6, "rank": "B", "lineno": 30,
            }],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["items"][0]["filepath"] == "app/services/payment.py"

    def test_all_item_fields_present(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # все 6 обязательных полей присутствуют в полном блоке
        radon_json = make_radon_output([{
            "filepath": "app/core.py",
            "blocks": [{
                "name": "run", "type": "function",
                "complexity": 7, "rank": "B", "lineno": 42,
            }],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert len(result["items"]) == 1
        assert_item_fields(result["items"][0])

    def test_missing_complexity_defaults_to_zero(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # отсутствие поля "complexity" — дефолт 0
        radon_json = make_radon_output([{
            "filepath": "app/module.py",
            "blocks": [{"name": "no_cc", "type": "function", "rank": "A", "lineno": 1}],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["items"][0]["complexity"] == 0

    def test_missing_rank_defaults_to_A(
        self, adapter, tmp_py_project, base_config, make_radon_output
    ):
        # отсутствие поля "rank" — дефолт "A"
        radon_json = make_radon_output([{
            "filepath": "app/module.py",
            "blocks": [{"name": "no_rank", "type": "method", "complexity": 3, "lineno": 1}],
        }])
        result = _run_with_output(adapter, tmp_py_project, base_config, radon_json)
        assert result["items"][0]["rank"] == "A"
