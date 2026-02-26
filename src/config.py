from __future__ import annotations

"""
Central configuration for tunable thresholds used across the pipeline.

These defaults are chosen to match the current behavior of the intake, graph
assembly, and deterministic checks. They can be overridden in the future by
reading from environment variables or a project-level config file.
"""

# Text-layer coherence threshold for hybrid extraction viability.
COHERENCE_THRESHOLD: float = 0.40

# Crown/invert heuristics for gravity systems.
CROWN_SPREAD_BUFFER_FT: float = 0.5
CROWN_RATIO_THRESHOLD: float = 10.0

# Extraction quality thresholds used in downstream checks.
QUALITY_WARNING_BAD_RATIO: float = 0.30
QUALITY_DEGRADATION_THRESHOLD: float = 0.30

# Station/offset tolerances for directional invert selection.
STATION_DELTA_THRESHOLD_FT: float = 0.5

# Ratio threshold for reclassifying slope mismatches as likely crown contamination.
CROWN_CONTAMINATION_RATIO_THRESHOLD: float = 5.0

