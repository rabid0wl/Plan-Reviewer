# Codex Task 003: Graph Assembly Fixes (Pipe Dedup, GB Filter, Slope Inverts)

## Context

Modules 5-6 (graph merge/assembly/checks) are structurally sound but the first real-data run exposed 5 issues. The two `flow_direction_error` findings on the SD graph are **false positives** caused by duplicate pipe edges with reversed direction. Fix these in priority order.

---

## Fix 1: Pipe Deduplication (CRITICAL)

**File:** `src/graph/assembly.py` — `build_utility_graph()`

**Problem:** The same physical pipe extracted from overlapping tiles creates multiple edges. The 342 LF SD pipe between STA 13+40.73 and 16+82.45 appears twice (`e3` and `e7`) with opposite direction. This causes false `flow_direction_error` findings.

**Root cause:** `merge.py` deduplicates structures but `assembly.py` creates a new edge for every pipe in every tile extraction, with no dedup.

**Fix:** After all edges are added, run a pipe dedup pass. Two edges are duplicates if they connect the same pair of structure nodes (in either direction) with matching size and similar slope/length.

Add a new function `_deduplicate_pipe_edges(graph)` called at the end of `build_utility_graph()`, before the quality summary is attached.

Dedup logic:
```python
def _deduplicate_pipe_edges(graph: nx.DiGraph) -> None:
    """Remove duplicate pipe edges between the same node pair, keeping the highest-confidence one."""
    # Group edges by unordered node pair + pipe signature
    edge_groups: dict[tuple, list[tuple[str, str, str, dict]]] = {}
    for u, v, data in graph.edges(data=True):
        # Normalize node pair to unordered so (A→B) and (B→A) group together
        pair = tuple(sorted([u, v]))
        size = (data.get("size") or "").upper().replace(" ", "")
        # Key: node pair + size. Slope/length may vary slightly between tiles.
        key = (pair, size)
        edge_id = data.get("edge_id", f"{u}->{v}")
        edge_groups.setdefault(key, []).append((u, v, edge_id, dict(data)))

    for key, edges in edge_groups.items():
        if len(edges) <= 1:
            continue

        # Pick the best edge: highest matched_confidence, then most metadata
        confidence_order = {"none": 0, "low": 1, "medium": 2, "high": 3}
        def edge_rank(e):
            d = e[3]
            conf = confidence_order.get(d.get("matched_confidence", "none"), 0)
            has_from = 1 if d.get("from_station") else 0
            has_to = 1 if d.get("to_station") else 0
            notes_len = len(d.get("notes") or "")
            return (conf, has_from + has_to, notes_len)

        best = max(edges, key=edge_rank)
        # Remove all others
        for u, v, edge_id, data in edges:
            if edge_id == best[2]:
                continue
            if graph.has_edge(u, v) and graph[u][v].get("edge_id") == edge_id:
                graph.remove_edge(u, v)
```

Also merge provenance from dropped duplicates into the kept edge: union `source_tile_ids`, `source_page_numbers`, `source_text_ids`.

**Test to add** (`tests/test_graph_assembly.py`):
- `test_pipe_dedup_keeps_highest_confidence`: Create two extractions from overlapping tiles that produce the same pipe between the same two structures. Verify only one edge survives, with merged provenance.
- `test_pipe_dedup_reversed_direction`: Same pipe seen from two tiles with opposite from/to direction. Verify dedup catches it.

**Expected result:** SD graph drops from 9 edges to ~5-6 unique pipes. Both `flow_direction_error` false positives disappear.

---

## Fix 2: Filter GB from SD Utility Graph

**File:** `src/graph/merge.py` — `_UTILITY_STRUCTURE_TYPES`

**Problem:** Grade breaks (BCR, ECR, BC, EC, PRC, GC) are curb grading features, not storm drain structures. They have TC/FL elevations but no pipe inverts. Including them in the SD graph creates 22 orphan nodes that need suppression.

**Change:** Remove `"GB"` from `_UTILITY_STRUCTURE_TYPES["SD"]`.

Change line 16 from:
```python
"SD": {"SDMH", "SDCB", "CB", "GB", "INLET", "DI", "CATCHBASIN"},
```
to:
```python
"SD": {"SDMH", "SDCB", "CB", "INLET", "DI", "CATCHBASIN"},
```

**Safety check:** To ensure we don't lose a rare GB that actually has pipe connections, add a fallback in `structure_matches_utility`: if `structure_type` is `"GB"` and the structure has at least one invert, include it regardless. This handles the edge case of a grade break that doubles as a junction structure.

To implement this, `structure_matches_utility` needs access to the structure's inverts. Update its signature:

```python
def structure_matches_utility(
    *,
    structure_type: str,
    utility_type: str,
    extraction_utility_types: list[str] | None = None,
    has_inverts: bool = False,
) -> bool:
    utility = utility_type.upper().strip()
    stype = _norm_token(structure_type)
    if not stype:
        return False

    if utility in stype:
        return True
    if stype in _UTILITY_STRUCTURE_TYPES.get(utility, set()):
        return True

    # GB with pipe inverts may be a junction — include it
    if stype == "GB" and has_inverts:
        return True

    return False
```

Update the call site in `merge_structures()` to pass `has_inverts=len(structure.inverts) > 0`.

**Test to update** (`tests/test_graph_merge.py`):
- Add `test_gb_without_inverts_excluded_from_sd`
- Add `test_gb_with_inverts_included_in_sd`

**Expected result:** SD node count drops from ~25 to ~8. Orphan suppression finding disappears. Quality grade may improve to B.

---

## Fix 3: Directional Invert Matching for Slope Check

**File:** `src/graph/checks.py` — `check_slope_consistency()`

**Problem:** Slope calculation uses `representative_invert` (min of all inverts), which may not be the invert connected to the specific pipe being checked. SDMH at 13+40.73 has INV E=300.8 and INV S=300.9 — using the min (300.8) for a pipe going S gives wrong slope.

**Change:** Before falling back to `representative_invert`, try to find the invert at each endpoint that faces the other endpoint. This requires knowing the pipe direction and matching it against invert directions.

Add a helper function:

```python
def _get_directional_invert(
    node_data: dict[str, Any],
    other_node_data: dict[str, Any],
    pipe_size: str | None = None,
) -> float | None:
    """Find the invert at this node that faces the other node, optionally matching pipe size."""
    inverts = node_data.get("inverts", [])
    if not inverts:
        return None

    my_station = node_data.get("station_ft")
    other_station = other_node_data.get("station_ft")

    if my_station is not None and other_station is not None:
        # Determine expected direction from this node to the other
        if other_station > my_station:
            preferred_dirs = {"E", "NE", "SE"}  # other node is upstream (higher station)
        else:
            preferred_dirs = {"W", "NW", "SW"}

        # First try: match direction AND pipe size
        if pipe_size:
            norm_size = pipe_size.upper().replace(" ", "")
            for inv in inverts:
                d = str(inv.get("direction", "")).upper()
                s = str(inv.get("pipe_size", "")).upper().replace(" ", "")
                if d in preferred_dirs and s == norm_size:
                    return float(inv["elevation"])

        # Second try: match direction only
        for inv in inverts:
            d = str(inv.get("direction", "")).upper()
            if d in preferred_dirs:
                return float(inv["elevation"])

    # Fallback: representative invert (min)
    return node_data.get("representative_invert")
```

In `check_slope_consistency()`, replace:
```python
upstream = graph.nodes[u].get("representative_invert")
downstream = graph.nodes[v].get("representative_invert")
```
with:
```python
pipe_size = data.get("size")
upstream = _get_directional_invert(graph.nodes[u], graph.nodes[v], pipe_size)
downstream = _get_directional_invert(graph.nodes[v], graph.nodes[u], pipe_size)
if upstream is None:
    upstream = graph.nodes[u].get("representative_invert")
if downstream is None:
    downstream = graph.nodes[v].get("representative_invert")
```

Also use the same directional lookup in `check_flow_direction()`.

**Test to add** (`tests/test_graph_checks.py`):
- `test_slope_uses_directional_invert`: Node A has INV E=300.0 and INV W=301.0. Node B is east of A. Verify slope check uses A's E invert (300.0), not the min (300.0 — same in this case, so use a case where they differ).

**Expected result:** The 17 LF pipe slope mismatch (labeled 0.0059 vs calculated 0.0118) should resolve or change if the correct directional invert is used.

---

## Fix 4: Re-tile and Re-extract with --no-cache

This is not a code change — just commands to run AFTER fixes 1-3 are implemented.

```bash
# Re-tile Page 14 with boundary recovery fix (already in text_layer.py)
python -m src.intake.tiler --pdf "References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf" --pages 14,19,34,36 --output output/intake-pass2

# Re-extract with --no-cache to get fresh extractions
python -m src.extraction.run_hybrid_batch --tiles-dir output/intake-pass2/tiles --text-layers-dir output/intake-pass2/text_layers --out-dir output/extractions/calibration-clean --max-tiles 24 --model "google/gemini-3-flash-preview" --timeout-sec 180 --no-cache

# Rebuild graphs
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SD --out output/graphs/calibration-clean-sd.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SS --out output/graphs/calibration-clean-ss.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type W --out output/graphs/calibration-clean-w.json

# Re-score
python -m src.extraction.score_calibration --extractions-dir output/extractions/calibration-clean
```

Verify:
- No "TALL 342 LF..." truncation in any text layer or extraction
- SD graph has ~5-6 unique pipe edges (not 9)
- Zero `flow_direction_error` false positives from duplicate reversed pipes
- GB nodes not present in SD graph (unless they have inverts)

---

## Fix 5: Investigate Sanitizer Rate

Not a code fix yet — just investigation.

After re-running with `--no-cache`, check how many tiles still need sanitizer recovery with the current compact prompt. Look at the meta files:

```bash
# Count sanitized tiles
python -c "
import json, pathlib
metas = sorted(pathlib.Path('output/extractions/calibration-clean').glob('*.meta.json'))
for m in metas:
    d = json.loads(m.read_text())
    if d.get('sanitized'):
        print(f\"{d['tile_id']}: sanitized\")
print(f'Total: {sum(1 for m in metas if json.loads(m.read_text()).get(\"sanitized\"))} / {len(metas)}')
"
```

If sanitizer rate is still >30%, check which required fields are failing validation. The compact prompt may need a one-line addition like "If a required field cannot be determined, omit the entire entity rather than setting required fields to null."

Report findings but don't change the prompt without reviewing the specific failure patterns first.

---

## Files Modified

| File | Changes |
|------|---------|
| `src/graph/assembly.py` | Add `_deduplicate_pipe_edges()`, call after edge creation |
| `src/graph/merge.py` | Remove GB from SD types, add `has_inverts` fallback to `structure_matches_utility` |
| `src/graph/checks.py` | Add `_get_directional_invert()`, use in slope + flow direction checks |
| `tests/test_graph_assembly.py` | Add pipe dedup tests |
| `tests/test_graph_merge.py` | Add GB filter tests |
| `tests/test_graph_checks.py` | Add directional invert test |

## Files NOT Modified

| File | Reason |
|------|--------|
| `src/extraction/*` | No extraction changes this task |
| `src/intake/*` | Boundary fix already shipped in Task 002 |
| `src/graph/__init__.py` | No export changes needed |

## Validation Checklist

After all code changes + re-tile/re-extract:

1. All unit tests pass (`python -m unittest discover -s tests -v`)
2. SD graph: ~3-8 structure nodes (no GBs unless they have inverts), ~5-6 pipe edges
3. Zero false-positive `flow_direction_error` from reversed duplicate pipes
4. Slope mismatches reduced (directional invert matching)
5. Calibration score still 9/10
6. No "TALL 342 LF..." in any extraction
7. Report sanitizer tile count and specific failure patterns
