# ===================================================================================================
# Report Aggregator (report_aggregator.py)
#
# Роль: агрегация и нормализация результатов всех статических адаптеров в единый отчет.
# Входные данные: context dict из pipeline.py + config dict.
# Выходные данные: AggregatedReport-совместимый dict (валидируется через schema.AggregatedReport).
#
# Этапы реализации:
#   Commit B — Шаги 1–2: нормализация + построение индексов (текущий файл)
#   Commit C — Шаги 3–4: кросс-резолюция и денормализация метрик
#   Commit D — Шаг 5:    одиночные события нарушений
#   Commit E — Шаг 6:    многоисточниковые события (LAYER_VIOLATION, OVERLOADED_CLASS)
#   Commit F — Шаги 7–9: дедупликация, сводка, финальная сборка
# ===================================================================================================

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from solid_dashboard.schema import (
    AggregatedReport,
    AggregatedSummary,
    ClassMetrics,
    DeadCodeEntry,
    EntitiesSection,
    FileMetrics,
    FunctionMetrics,
    LayerMetrics,
    ReportMeta,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# CC threshold mirrors the hardcoded value in radon_adapter.py: `if complexity > 10`
# NOT read from config — RadonAdapter has no cc_threshold config key.
CC_THRESHOLD: int = 10

# Adapter keys as they appear in the context dict populated by pipeline.py
_ADAPTER_KEYS: Tuple[str, ...] = ("radon", "cohesion", "import_graph", "import_linter", "pyan3")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def aggregate_results(context: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Aggregates raw adapter results into a single structured report.

    Parameters
    ----------
    context : Dict[str, Any]
        Pipeline context dict populated by run_pipeline(). Expected keys:
        "radon", "cohesion", "import_graph", "import_linter", "pyan3".
        All keys are optional — absent or error-containing values degrade gracefully.
    config : Dict[str, Any]
        The same config dict passed to run_pipeline() and each adapter.
        Config keys consumed by the aggregator:
          config.get("cohesion_threshold", 1)  -> lcom4_threshold (int)
          config.get("layers", {})             -> layer prefix map for module->layer resolution
          config.get("utility_layers", {})     -> crosscutting layer names (no tier)
        Note: CC threshold (10) is a module constant (CC_THRESHOLD), not read from config.

    Returns
    -------
    Dict[str, Any]
        AggregatedReport-shaped dict. Always valid — missing adapter data produces
        empty lists/zeroes, never absent keys.
        Validate with: AggregatedReport.model_validate(result)
    """
    if config is None:
        config = {}

    config_defaults_used: bool = not bool(config)

    # -----------------------------------------------------------------------
    # Step 1 — Guard and normalize raw adapter outputs
    # Extract thresholds from config (with defaults matching adapter defaults)
    # -----------------------------------------------------------------------
    # CC_THRESHOLD = 10 (module constant — hardcoded in RadonAdapter, NOT configurable)
    lcom4_threshold: int = int(config.get("cohesion_threshold", 1))  # cohesion_adapter.py key
    layer_config: Dict[str, Any] = config.get("layers", {})
    utility_layers: Dict[str, Any] = config.get("utility_layers", {})

    adapters_succeeded: List[str] = []
    adapters_failed: List[str] = []

    radon_fns, mi_files = _safe_normalize(
        "radon", context, _normalize_radon,
        adapters_succeeded, adapters_failed,
        default=([], []),
    )

    cohesion_classes: List[ClassMetrics] = _safe_normalize(
        "cohesion", context, _normalize_cohesion,
        adapters_succeeded, adapters_failed,
        default=[],
    )

    graph_layers, graph_edges, graph_violations = _safe_normalize(
        "import_graph", context, _normalize_import_graph,
        adapters_succeeded, adapters_failed,
        default=([], [], []),
    )

    contract_violations: List[Dict[str, Any]] = _safe_normalize(
        "import_linter", context, _normalize_import_linter,
        adapters_succeeded, adapters_failed,
        default=[],
    )

    _pyan3_nodes, dead_entries = _safe_normalize(
        "pyan3", context, _normalize_pyan3,
        adapters_succeeded, adapters_failed,
        default=([], []),
    )

    lizard_used: bool = bool(
        isinstance(context.get("radon"), dict) and context["radon"].get("lizard_used", False)
    )

    # -----------------------------------------------------------------------
    # Step 2 — Build entity indexes
    # -----------------------------------------------------------------------
    file_index: Dict[str, FileMetrics] = _build_file_index(radon_fns, mi_files, cohesion_classes)
    class_index: Dict[str, ClassMetrics] = _build_class_index(cohesion_classes)
    fn_index: Dict[str, FunctionMetrics] = _build_function_index(radon_fns)
    layer_index: Dict[str, LayerMetrics] = _build_layer_index(graph_layers)

    # -----------------------------------------------------------------------
    # Steps 3–9 implemented in Commits C–F.
    # Expose unused variables to avoid linter warnings; they will be consumed later.
    # -----------------------------------------------------------------------
    _ = (lcom4_threshold, layer_config, utility_layers,
         graph_edges, graph_violations, contract_violations)

    # -----------------------------------------------------------------------
    # Assemble report (entities + meta only at this stage)
    # -----------------------------------------------------------------------
    meta = ReportMeta(
        generated_at=datetime.now(tz=timezone.utc).isoformat(),
        adapter_versions_available=list(_ADAPTER_KEYS),
        adapters_succeeded=adapters_succeeded,
        adapters_failed=adapters_failed,
        lizard_used=lizard_used,
        config_defaults_used=config_defaults_used,
    )

    entities = EntitiesSection(
        files=sorted(file_index.values(), key=lambda f: f.filepath),
        classes=sorted(class_index.values(), key=lambda c: c.class_id),
        functions=sorted(fn_index.values(), key=lambda fn: fn.function_id),
        layers=sorted(layer_index.values(), key=lambda la: la.layer_name),
    )

    report = AggregatedReport(
        meta=meta,
        summary=AggregatedSummary(),  # populated in Commit F (_compute_summary)
        entities=entities,
        violations=[],                 # populated in Commits D–F
        dead_code=dead_entries,
    )

    return report.model_dump()


# ---------------------------------------------------------------------------
# Internal helper: safe normalization with per-adapter error isolation
# ---------------------------------------------------------------------------

def _safe_normalize(
    key: str,
    context: Dict[str, Any],
    normalize_fn,
    succeeded: List[str],
    failed: List[str],
    default: Any,
) -> Any:
    """
    Calls normalize_fn(raw) for context[key], isolating failures per adapter.

    On missing key, error result, or exception:
      marks adapter as failed, returns default.
    On success:
      marks adapter as succeeded, returns result.
    """
    raw = context.get(key)
    if raw is None or _is_error_result(raw):
        failed.append(key)
        return default
    try:
        result = normalize_fn(raw)
        succeeded.append(key)
        return result
    except Exception:
        failed.append(key)
        return default


def _is_error_result(raw: Any) -> bool:
    """
    Returns True if an adapter result signals failure.

    Covers two error patterns used across adapters:
      {"error": "..."}           — radon_adapter, import_graph_adapter, import_linter_adapter
      {"is_success": False, ...} — import_linter_adapter, pyan3_adapter
    """
    if not isinstance(raw, dict):
        return True
    if "error" in raw:
        return True
    if raw.get("is_success") is False:
        return True
    return False


# ---------------------------------------------------------------------------
# Normalizers — one per adapter
# ---------------------------------------------------------------------------

def _normalize_radon(
    raw: Dict[str, Any],
) -> Tuple[List[FunctionMetrics], List[Dict[str, Any]]]:
    """
    Normalizes RadonAdapter output into:
      - FunctionMetrics list (one per function/method item)
      - raw MI file dicts (passed to _build_file_index; shape: {filepath, mi, rank})

    function_id format: "<filepath>::<lineno>::<name>"
    """
    fns: List[FunctionMetrics] = []

    for item in raw.get("items", []):
        fp: str = item.get("filepath", "")
        lineno: int = item.get("lineno", 0)
        name: str = item.get("name", "")

        fns.append(FunctionMetrics(
            function_id=f"{fp}::{lineno}::{name}",
            filepath=fp,
            name=name,
            type=item.get("type", "function"),
            lineno=lineno,
            cc=item.get("complexity", 0),
            rank=item.get("rank", "A"),
            parameter_count=item.get("parameter_count"),  # None if Lizard not used
        ))

    # MI files: keep as raw dicts; FileMetrics merger consumes them in _build_file_index
    mi_raw = raw.get("maintainability") or {}
    mi_files: List[Dict[str, Any]] = (
        mi_raw.get("files", []) if isinstance(mi_raw, dict) else []
    )

    return fns, mi_files


def _normalize_cohesion(raw: Dict[str, Any]) -> List[ClassMetrics]:
    """
    Normalizes CohesionAdapter output into ClassMetrics list.

    IMPORTANT — D1 correction (SOLID_audit.md):
      The raw CohesionAdapter field is "name", NOT "class_name".
      This normalizer reads record["name"] and maps it to class_name in ClassMetrics.

    class_id format: "<filepath>::<class_name>"
    """
    classes: List[ClassMetrics] = []

    for record in raw.get("classes", []):
        raw_name: str = record.get("name", "")      # raw field is "name" — see D1 correction
        fp: str = record.get("filepath", "")
        class_id: str = f"{fp}::{raw_name}"

        lcom4_val = record.get("cohesion_score")    # cohesion_score == LCOM4

        classes.append(ClassMetrics(
            class_id=class_id,
            filepath=fp,
            class_name=raw_name,                    # normalized: "name" -> class_name
            lineno=record.get("lineno", 0),
            class_kind=record.get("class_kind", "concrete"),
            lcom4=float(lcom4_val) if lcom4_val is not None else None,
            lcom4_norm=record.get("cohesion_score_norm"),
            methods_count=record.get("methods_count", 0),
            excluded_from_aggregation=record.get("excluded_from_aggregation", False),
            label=raw_name,                         # label mirrors class_name for rendering
        ))

    return classes


def _normalize_import_graph(
    raw: Dict[str, Any],
) -> Tuple[List[LayerMetrics], List[Dict[str, str]], List[Dict[str, Any]]]:
    """
    Normalizes ImportGraphAdapter output into:
      - LayerMetrics list (nodes with Ca/Ce/Instability)
      - raw edge list  [{source, target}] — passed through verbatim
      - raw violation list (SDP-001 / SLP-001 dicts) — passed through verbatim

    D2 correction (SOLID_audit.md):
      Raw node dict includes "label" field (always equals "id").
      LayerMetrics.label is populated from node["label"].

    tier is not set here (requires config layer_order); resolved in Commit C.
    """
    layers: List[LayerMetrics] = []

    for node in raw.get("nodes", []):
        layer_name: str = node.get("id", "")

        layers.append(LayerMetrics(
            layer_id=layer_name,
            layer_name=layer_name,
            label=node.get("label", layer_name),    # "label" always equals id — see D2 correction
            tier=None,                               # resolved in Commit C via config layer_order
            ca=node.get("ca", 0),
            ce=node.get("ce", 0),
            instability=node.get("instability", 0.0),
        ))

    edges: List[Dict[str, str]] = raw.get("edges", [])
    violations: List[Dict[str, Any]] = raw.get("violations", [])

    return layers, edges, violations


def _normalize_import_linter(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Normalizes ImportLinterAdapter output.

    Returns the violation_details list verbatim; each element has shape:
      {
        "contract_name": str,
        "status": "BROKEN",
        "broken_imports": [{"importer": str, "imported": str}, ...]
      }
    """
    return raw.get("violation_details", [])


def _normalize_pyan3(
    raw: Dict[str, Any],
) -> Tuple[List[str], List[DeadCodeEntry]]:
    """
    Normalizes Pyan3Adapter output into:
      - node list (qualified name strings, for future use)
      - DeadCodeEntry list

    Confidence assignment:
      Pyan3Adapter reports dead_nodes as a flat list (no per-node confidence field).
      Global confidence is derived from collision_rate:
        collision_rate >= 0.35  -> "low"  (parse quality suspect)
        collision_rate <  0.35  -> "high" (parse quality acceptable)
      Threshold 0.35 matches the pyan3.collision_rate_threshold in solid_config.json.
    """
    collision_rate: float = float(raw.get("collision_rate", 0.0))
    global_confidence: str = "low" if collision_rate >= 0.35 else "high"

    dead_entries: List[DeadCodeEntry] = [
        DeadCodeEntry(
            dead_id=qname,
            qualified_name=qname,
            confidence=global_confidence,
        )
        for qname in raw.get("dead_nodes", [])
    ]

    nodes: List[str] = raw.get("nodes", [])
    return nodes, dead_entries


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------

def _build_file_index(
    fns: List[FunctionMetrics],
    mi_files: List[Dict[str, Any]],
    cohesion_classes: List[ClassMetrics],
) -> Dict[str, FileMetrics]:
    """
    Builds filepath -> FileMetrics index by aggregating:
      - per-file CC metrics from FunctionMetrics list
      - MI data from raw MI file dicts (shape: {filepath, mi, rank})
      - class count from ClassMetrics list
    """
    cc_by_file: Dict[str, List[int]] = defaultdict(list)
    for fn in fns:
        cc_by_file[fn.filepath].append(fn.cc)

    class_count_by_file: Dict[str, int] = defaultdict(int)
    for cls in cohesion_classes:
        class_count_by_file[cls.filepath] += 1

    mi_lookup: Dict[str, Dict[str, Any]] = {
        rec["filepath"]: rec
        for rec in mi_files
        if isinstance(rec, dict) and "filepath" in rec
    }

    all_fps = set(cc_by_file.keys()) | set(mi_lookup.keys()) | set(class_count_by_file.keys())

    index: Dict[str, FileMetrics] = {}
    for fp in sorted(all_fps):
        cc_list = cc_by_file.get(fp, [])
        mi_rec = mi_lookup.get(fp)

        index[fp] = FileMetrics(
            file_id=fp,
            filepath=fp,
            mi=float(mi_rec["mi"]) if mi_rec else None,
            mi_rank=mi_rec.get("rank") if mi_rec else None,
            function_count=len(cc_list),
            mean_cc=round(sum(cc_list) / len(cc_list), 2) if cc_list else 0.0,
            max_cc=max(cc_list) if cc_list else 0,
            high_cc_count=sum(1 for cc in cc_list if cc > CC_THRESHOLD),
            class_count=class_count_by_file.get(fp, 0),
        )

    return index


def _build_class_index(classes: List[ClassMetrics]) -> Dict[str, ClassMetrics]:
    """
    Builds class_id -> ClassMetrics index.
    class_id format: "<filepath>::<class_name>"
    """
    return {cls.class_id: cls for cls in classes}


def _build_function_index(fns: List[FunctionMetrics]) -> Dict[str, FunctionMetrics]:
    """
    Builds function_id -> FunctionMetrics index.
    function_id format: "<filepath>::<lineno>::<name>"
    """
    return {fn.function_id: fn for fn in fns}


def _build_layer_index(layers: List[LayerMetrics]) -> Dict[str, LayerMetrics]:
    """
    Builds layer_name -> LayerMetrics index.
    """
    return {layer.layer_name: layer for layer in layers}
