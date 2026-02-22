"""Graph assembly and deterministic checks."""

__all__ = [
    "Finding",
    "MergedStructure",
    "build_utility_graph",
    "check_connectivity",
    "check_elevation_consistency",
    "check_flow_direction",
    "check_pipe_size_consistency",
    "check_slope_consistency",
    "graph_to_dict",
    "load_extractions_with_meta",
    "merge_structures",
    "run_all_checks",
    "structure_matches_utility",
]


def __getattr__(name: str):
    if name in {"build_utility_graph", "graph_to_dict", "load_extractions_with_meta"}:
        from .assembly import build_utility_graph, graph_to_dict, load_extractions_with_meta

        return {
            "build_utility_graph": build_utility_graph,
            "graph_to_dict": graph_to_dict,
            "load_extractions_with_meta": load_extractions_with_meta,
        }[name]
    if name in {
        "Finding",
        "check_connectivity",
        "check_elevation_consistency",
        "check_flow_direction",
        "check_pipe_size_consistency",
        "check_slope_consistency",
        "run_all_checks",
    }:
        from .checks import (
            Finding,
            check_connectivity,
            check_elevation_consistency,
            check_flow_direction,
            check_pipe_size_consistency,
            check_slope_consistency,
            run_all_checks,
        )

        return {
            "Finding": Finding,
            "check_connectivity": check_connectivity,
            "check_elevation_consistency": check_elevation_consistency,
            "check_flow_direction": check_flow_direction,
            "check_pipe_size_consistency": check_pipe_size_consistency,
            "check_slope_consistency": check_slope_consistency,
            "run_all_checks": run_all_checks,
        }[name]
    if name in {"MergedStructure", "merge_structures", "structure_matches_utility"}:
        from .merge import MergedStructure, merge_structures, structure_matches_utility

        return {
            "MergedStructure": MergedStructure,
            "merge_structures": merge_structures,
            "structure_matches_utility": structure_matches_utility,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
