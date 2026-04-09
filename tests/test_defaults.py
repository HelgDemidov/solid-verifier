# ===================================================================================================
# Юнит-тесты для solid_dashboard/defaults.py
#
# Цель: зафиксировать значения, типы и импортируемость всех четырёх констант.
# Гарантирует, что случайное изменение значения или типа будет немедленно поймано.
#
# Покрываемые сценарии:
#   D1 — CC_THRESHOLD: значение=10, тип=int
#   D2 — LCOM4_THRESHOLD: значение=1, тип=int
#   D3 — LOW_MI_RANK: значение="C", тип=str, длина=1
#   D4 — DEAD_CODE_CONFIDENCE_CUTOFF: значение=0.35, тип=float, диапазон (0, 1)
#   D5 — CC_THRESHOLD переэкспортируется radon_adapter на уровне модуля с тем же значением
# ===================================================================================================

import pytest


# ---------------------------------------------------------------------------
# D1 — CC_THRESHOLD
# ---------------------------------------------------------------------------

def test_d1_cc_threshold_value():
    """CC_THRESHOLD должен быть int со значением 10."""
    from solid_dashboard.defaults import CC_THRESHOLD

    assert CC_THRESHOLD == 10, (
        f"Expected CC_THRESHOLD=10, got {CC_THRESHOLD}"
    )
    assert isinstance(CC_THRESHOLD, int), (
        f"Expected CC_THRESHOLD to be int, got {type(CC_THRESHOLD).__name__}"
    )


# ---------------------------------------------------------------------------
# D2 — LCOM4_THRESHOLD
# ---------------------------------------------------------------------------

def test_d2_lcom4_threshold_value():
    """LCOM4_THRESHOLD должен быть int со значением 1."""
    from solid_dashboard.defaults import LCOM4_THRESHOLD

    assert LCOM4_THRESHOLD == 1, (
        f"Expected LCOM4_THRESHOLD=1, got {LCOM4_THRESHOLD}"
    )
    assert isinstance(LCOM4_THRESHOLD, int), (
        f"Expected LCOM4_THRESHOLD to be int, got {type(LCOM4_THRESHOLD).__name__}"
    )


# ---------------------------------------------------------------------------
# D3 — LOW_MI_RANK
# ---------------------------------------------------------------------------

def test_d3_low_mi_rank_value():
    """LOW_MI_RANK должен быть однобуквенной строкой "C"."""
    from solid_dashboard.defaults import LOW_MI_RANK

    assert LOW_MI_RANK == "C", (
        f"Expected LOW_MI_RANK='C', got {LOW_MI_RANK!r}"
    )
    assert isinstance(LOW_MI_RANK, str), (
        f"Expected LOW_MI_RANK to be str, got {type(LOW_MI_RANK).__name__}"
    )
    # ранг — всегда одна буква (A/B/C/D/E/F)
    assert len(LOW_MI_RANK) == 1, (
        f"Expected LOW_MI_RANK to be a single character, got {LOW_MI_RANK!r} (len={len(LOW_MI_RANK)})"
    )


# ---------------------------------------------------------------------------
# D4 — DEAD_CODE_CONFIDENCE_CUTOFF
# ---------------------------------------------------------------------------

def test_d4_dead_code_confidence_cutoff_value():
    """DEAD_CODE_CONFIDENCE_CUTOFF должен быть float=0.35, строго внутри (0, 1)."""
    from solid_dashboard.defaults import DEAD_CODE_CONFIDENCE_CUTOFF

    assert DEAD_CODE_CONFIDENCE_CUTOFF == pytest.approx(0.35), (
        f"Expected DEAD_CODE_CONFIDENCE_CUTOFF=0.35, got {DEAD_CODE_CONFIDENCE_CUTOFF}"
    )
    assert isinstance(DEAD_CODE_CONFIDENCE_CUTOFF, float), (
        f"Expected DEAD_CODE_CONFIDENCE_CUTOFF to be float, "
        f"got {type(DEAD_CODE_CONFIDENCE_CUTOFF).__name__}"
    )
    # должен быть строго внутри (0, 1) — граничные значения 0.0 и 1.0 бессмысленны
    assert 0.0 < DEAD_CODE_CONFIDENCE_CUTOFF < 1.0, (
        f"Expected DEAD_CODE_CONFIDENCE_CUTOFF in open interval (0, 1), "
        f"got {DEAD_CODE_CONFIDENCE_CUTOFF}"
    )


# ---------------------------------------------------------------------------
# D5 — CC_THRESHOLD переэкспортируется radon_adapter на уровне модуля
# ---------------------------------------------------------------------------

def test_d5_cc_threshold_reexported_by_radon_adapter():
    """
    radon_adapter импортирует CC_THRESHOLD из defaults на уровне модуля
    (строка: from solid_dashboard.defaults import CC_THRESHOLD).
    Значение должно совпадать с defaults.CC_THRESHOLD.
    Тест ловит рассинхронизацию, если radon_adapter начнёт хардкодить своё значение.
    """
    from solid_dashboard.defaults import CC_THRESHOLD as cc_from_defaults
    # импорт на уровне модуля -> атрибут модуля radon_adapter
    from solid_dashboard.adapters.radon_adapter import CC_THRESHOLD as cc_from_adapter

    assert cc_from_defaults == cc_from_adapter, (
        f"CC_THRESHOLD mismatch: defaults={cc_from_defaults}, "
        f"radon_adapter={cc_from_adapter}. "
        "radon_adapter must import CC_THRESHOLD from defaults, not hardcode it."
    )
    assert cc_from_adapter == 10
