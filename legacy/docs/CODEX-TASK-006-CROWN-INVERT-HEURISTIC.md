# Codex Task 006: Crown/Invert Post-Processing Heuristic

## Context

The Gemini vision model sometimes puts **crown elevation readings (CR)** into the `inverts[]` array instead of only true invert elevations (IE/INV). On the Dinuba Corridor plan set (pages 24-25), this causes 4 false `slope_mismatch` warnings where all labeled slopes are 0.0011 but calculated slopes range from 0.0088 to 0.1279 — wildly off because the "inverts" at some nodes are actually crown readings ~4-5 feet above the true inverts.

We tried fixing this with prompt instructions (CROWN vs INVERT DISTINCTION section in `prompts.py`), but the model ignores them. We need a **deterministic Python post-processing heuristic** that filters suspect crown readings at graph assembly time.

**Scope:** This task fixes false-positive slope warnings caused by crown contamination. It does NOT address sanitizer rate, extraction quality grade, or tile-level issues — those are separate tasks.

---

## Evidence from Corridor SD Graph

Node `SD:24:INLET:1083.34:+19.95`:
```json
"inverts": [
  {"direction":"W","pipe_size":"18\"","elevation":320.48},
  {"direction":"N","pipe_size":"18\"","elevation":325.14}
]
"fl_elevation": 328.88
```
The 325.14 is 4.66' above 320.48. For an 18" pipe (1.5' diameter), the crown should be at most ~invert + 1.5'. A 4.66' gap means 325.14 is NOT an invert — it's a crown or misread elevation.

Node `SD:24:INLET:1134.29:+58.62`:
```json
"inverts": [{"direction":"W","pipe_size":"18\"","elevation":325.34}]
"fl_elevation": 328.95
```
Single invert that is actually a crown — can't filter locally, needs cross-edge comparison.

Node `SD:24:INLET:1134.51:+20.13`:
```json
"inverts": [
  {"direction":"E","pipe_size":"18\"","elevation":325.3},
  {"direction":"S","pipe_size":"18\"","elevation":325.2}
]
"fl_elevation": 328.69
```
Both inverts are crowns (differ by only 0.1'), needs cross-edge comparison.

All 4 slope mismatch findings (labeled 0.0011 vs calculated 0.0088–0.1279) are caused by these crown readings being used as inverts in the slope calculation.

---

## Implementation

### Part 1: Per-Node Crown Filtering — `src/graph/assembly.py`

Add a helper and a new function:

**Helper: `_parse_pipe_diameter_ft`**
```python
def _parse_pipe_diameter_ft(size_str: str | None) -> float | None:
    """Parse pipe size string like '18\"' or '36\"' to diameter in feet."""
    if not size_str:
        return None
    match = re.search(r'(\d+(?:\.\d+)?)', str(size_str))
    if not match:
        return None
    inches = float(match.group(1))
    return inches / 12.0
```

**Function: `_filter_suspect_crowns`**

Add `_filter_suspect_crowns(graph)` that runs AFTER all nodes and edges are added, but BEFORE `_deduplicate_pipe_edges` and `_orient_gravity_edges`.

**IMPORTANT: Only run for gravity utilities (SD, SS). Skip for W (pressure system — crown/invert distinction doesn't apply).**

```python
def _filter_suspect_crowns(graph: nx.DiGraph) -> None:
    """Filter likely crown elevations from gravity pipe inverts."""
    utility = str(graph.graph.get("utility_type", "")).upper()
    if utility not in {"SD", "SS"}:
        return
    # ... pass 1 and pass 2 below
```

**Pass 1 — Multi-invert spread check (nodes with 2+ inverts):**
For each structure node with 2+ inverts:
1. Parse the largest pipe diameter from that node's inverts' `pipe_size` fields using `_parse_pipe_diameter_ft`.
2. Compute `min_elev = min(invert elevations)` and `max_elev = max(invert elevations)`.
3. If `max_elev - min_elev > max_pipe_diameter_ft + 0.5`:
   - Any invert with elevation > `min_elev + max_pipe_diameter_ft + 0.5` is a suspect crown.
   - Move those inverts from the node's `inverts` list to a new node attribute `crown_suspects` (list of dicts, same format).
   - Recompute `representative_invert` = min(remaining invert elevations).
   - Log at debug level.

**Pass 2 — Cross-edge comparison (catches single-invert crown nodes):**
For each edge that has BOTH a numeric `slope` (labeled) and numeric `length_lf`:
1. `expected_drop = abs(float(labeled_slope)) * float(length_lf)`
2. Get `from_inv = representative_invert` of from-node, `to_inv = representative_invert` of to-node.
3. If both are available: `actual_drop = abs(from_inv - to_inv)`
4. If `actual_drop > expected_drop * 10` AND `actual_drop > 2.0` feet:
   - Mark the **edge** with `crown_contamination_candidate = True`
   - On the node with the HIGHER representative_invert, set `suspect_crown = True`
   - Do NOT modify inverts in this pass (lower confidence than pass 1).

**IMPORTANT — Edge-level marking:** Don't just set `suspect_crown = True` globally on a node. Also mark the specific edge as `crown_contamination_candidate = True`. This prevents one bad edge from tainting all slope checks on a shared node. In checks.py, use the edge-level flag as the primary signal.

**Integration point in `build_utility_graph()`:**

Current code at the end of `build_utility_graph()`:
```python
    _deduplicate_pipe_edges(graph)
    _orient_gravity_edges(graph)
```

Change to:
```python
    _filter_suspect_crowns(graph)
    _deduplicate_pipe_edges(graph)
    _orient_gravity_edges(graph)
```

---

### Part 2: Crown-Aware Slope Check — `src/graph/checks.py`

Modify `check_slope_consistency` to detect and reclassify crown-contaminated slope mismatches.

After the existing tolerance check (`if abs(calculated - float(labeled_slope)) <= tolerance: continue`), add crown detection logic BEFORE the existing `findings.append(...)`:

```python
# Detect likely crown contamination
labeled_abs = abs(float(labeled_slope))
slope_ratio = calculated / labeled_abs if labeled_abs > 0 else float('inf')

# Check EDGE-level flag first (most precise), then node-level signals
edge_flagged = bool(data.get("crown_contamination_candidate", False))
from_crowns = graph.nodes[u].get("crown_suspects", [])
to_crowns = graph.nodes[v].get("crown_suspects", [])

if slope_ratio > 5.0 and (edge_flagged or from_crowns or to_crowns):
    # Crown contamination — emit info-level finding instead of warning
    findings.append(Finding(
        finding_type="crown_contamination",
        severity="info",
        description=(
            f"Likely crown/invert confusion on edge {_edge_id(u, v, data)}: "
            f"labeled slope {float(labeled_slope):.4f}, calculated {calculated:.4f} "
            f"(ratio {slope_ratio:.1f}x). Endpoint inverts may contain crown readings."
        ),
        source_sheets=source_sheets,
        source_text_ids=source_text_ids,
        node_ids=[u, v],
        edge_ids=[_edge_id(u, v, data)],
        expected_value=f"{float(labeled_slope):.4f}",
        actual_value=f"{calculated:.4f}",
    ))
    continue  # Skip the normal slope_mismatch finding below
```

Key details:
- Use `abs(float(labeled_slope))` to handle negative/zero slopes safely.
- Check `edge_flagged` (from pass 2's `crown_contamination_candidate`) as the primary signal — this is scoped to the specific edge, not globally to the node.
- Also check `crown_suspects` (from pass 1's multi-invert filtering) as a secondary signal.
- This turns the false `slope_mismatch` warnings into `crown_contamination` info findings.

---

### Part 3: Unit Tests — `tests/test_graph_checks.py`

Add these test cases (at minimum) to the existing `GraphChecksTests` class:

**Test 1: Multi-invert crown removed to crown_suspects**
Build a graph with one structure node that has inverts at 320.48 and 325.14 (both 18" pipe). After `_filter_suspect_crowns(graph)`, assert:
- `crown_suspects` on the node contains the 325.14 invert
- `inverts` on the node only contains the 320.48 invert
- `representative_invert` == 320.48

**Test 2: Single-invert high-drop edge sets crown contamination candidate**
Build a graph with two nodes: upstream repr_invert=325.3, downstream repr_invert=320.25, edge with slope=0.0011, length=51. After `_filter_suspect_crowns(graph)`, assert:
- Edge has `crown_contamination_candidate = True`
- Upstream node has `suspect_crown = True`

**Test 3: Slope mismatch reclassified to crown_contamination when flagged**
Build a graph with a slope mismatch (labeled 0.0011, calculated ~0.0931) where the edge has `crown_contamination_candidate=True`. Run `check_slope_consistency(graph)`. Assert:
- Finding type is `crown_contamination`, NOT `slope_mismatch`
- Severity is `info`, NOT `warning`

**Test 4: Non-crown slope mismatches remain as warnings**
Build a graph with a genuine slope mismatch (labeled 0.005, calculated 0.010 → ratio 2.0x) where no crown flags are set. Run `check_slope_consistency(graph)`. Assert:
- Finding type is `slope_mismatch`
- Severity is `warning`

**Test 5: Crown filter skips water utility**
Build a W graph with inverts that have a large spread. Run `_filter_suspect_crowns(graph)`. Assert inverts are unchanged (no filtering applied to pressure systems).

---

## Validation

This task only modifies graph-layer code (assembly.py, checks.py). **Reuse existing extractions** — no need to re-tile or re-extract. Just regenerate graphs, findings, and report.

### Step 1: Run unit tests

```bash
python -m pytest tests/test_graph_checks.py -v
python -m pytest tests/ -v
```

### Step 2: Regenerate corridor graphs + findings + report

Existing extractions are at `output/extractions/corridor-u1u2-postfix/`. Regenerate downstream artifacts:

```bash
# Rebuild graphs (runs the crown filter in assembly.py)
python -m src.graph.assembly --extractions-dir output/extractions/corridor-u1u2-postfix --utility-type SD --out output/graphs/corridor-u1u2-crownfix-sd.json
python -m src.graph.assembly --extractions-dir output/extractions/corridor-u1u2-postfix --utility-type SS --out output/graphs/corridor-u1u2-crownfix-ss.json
python -m src.graph.assembly --extractions-dir output/extractions/corridor-u1u2-postfix --utility-type W  --out output/graphs/corridor-u1u2-crownfix-w.json

# Generate findings (run checks on each graph, write JSON)
python -c "
import json
from pathlib import Path
from src.graph.assembly import build_utility_graph, graph_to_dict, load_extractions_with_meta
from src.graph.checks import run_all_checks

extractions_dir = Path('output/extractions/corridor-u1u2-postfix')
findings_dir = Path('output/graphs/findings')
findings_dir.mkdir(parents=True, exist_ok=True)
prefix = 'corridor-u1u2-crownfix'

extractions, tile_meta = load_extractions_with_meta(extractions_dir)
for utility in ('SD', 'SS', 'W'):
    graph = build_utility_graph(extractions=extractions, utility_type=utility, tile_meta_by_id=tile_meta)
    findings = run_all_checks(graph)
    payload = {
        'utility_type': utility,
        'graph': {
            'nodes': graph.number_of_nodes(),
            'edges': graph.number_of_edges(),
            'quality_summary': graph.graph.get('quality_summary', {}),
        },
        'counts': {
            'total_findings': len(findings),
            'by_severity': {},
            'by_type': {},
        },
        'findings': [f.to_dict() for f in findings],
    }
    for f in findings:
        payload['counts']['by_severity'][f.severity] = payload['counts']['by_severity'].get(f.severity, 0) + 1
        payload['counts']['by_type'][f.finding_type] = payload['counts']['by_type'].get(f.finding_type, 0) + 1
    out_path = findings_dir / f'{prefix}-{utility.lower()}-findings.json'
    out_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(f'{utility}: {len(findings)} findings -> {out_path}')
"

# Generate report
python -m src.report.html_report \
  --graphs-dir output/graphs \
  --findings-dir output/graphs/findings \
  --prefix corridor-u1u2-crownfix \
  --batch-summary output/extractions/corridor-u1u2-postfix/batch_summary.json \
  --out output/reports/corridor-u1u2-crownfix-report.html
```

### Step 3: Regression check on FNC Farms

```bash
# Rebuild graphs
python -m src.graph.assembly --extractions-dir output/extractions/calibration-postfix --utility-type SD --out output/graphs/calibration-crownfix-sd.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-postfix --utility-type SS --out output/graphs/calibration-crownfix-ss.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-postfix --utility-type W  --out output/graphs/calibration-crownfix-w.json

# Generate findings
python -c "
import json
from pathlib import Path
from src.graph.assembly import build_utility_graph, graph_to_dict, load_extractions_with_meta
from src.graph.checks import run_all_checks

extractions_dir = Path('output/extractions/calibration-postfix')
findings_dir = Path('output/graphs/findings')
findings_dir.mkdir(parents=True, exist_ok=True)
prefix = 'calibration-crownfix'

extractions, tile_meta = load_extractions_with_meta(extractions_dir)
for utility in ('SD', 'SS', 'W'):
    graph = build_utility_graph(extractions=extractions, utility_type=utility, tile_meta_by_id=tile_meta)
    findings = run_all_checks(graph)
    payload = {
        'utility_type': utility,
        'graph': {
            'nodes': graph.number_of_nodes(),
            'edges': graph.number_of_edges(),
            'quality_summary': graph.graph.get('quality_summary', {}),
        },
        'counts': {
            'total_findings': len(findings),
            'by_severity': {},
            'by_type': {},
        },
        'findings': [f.to_dict() for f in findings],
    }
    for f in findings:
        payload['counts']['by_severity'][f.severity] = payload['counts']['by_severity'].get(f.severity, 0) + 1
        payload['counts']['by_type'][f.finding_type] = payload['counts']['by_type'].get(f.finding_type, 0) + 1
    out_path = findings_dir / f'{prefix}-{utility.lower()}-findings.json'
    out_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
    print(f'{utility}: {len(findings)} findings -> {out_path}')
"

# Generate report
python -m src.report.html_report \
  --graphs-dir output/graphs \
  --findings-dir output/graphs/findings \
  --prefix calibration-crownfix \
  --batch-summary output/extractions/calibration-postfix/batch_summary.json \
  --out output/reports/calibration-crownfix-report.html
```

**Note:** If the extraction directories above don't exist with those exact names, check `output/extractions/` for the most recent postfix run directories and adjust paths accordingly.

---

## Files to Modify

1. **`src/graph/assembly.py`** — Add `_filter_suspect_crowns()`, `_parse_pipe_diameter_ft()`, call from `build_utility_graph()` (SD/SS only)
2. **`src/graph/checks.py`** — Add crown-contamination detection to `check_slope_consistency()` (use `abs()` for labeled slope)
3. **`tests/test_graph_checks.py`** — Add 5 test cases for crown detection

## Files NOT to Modify

- `src/extraction/prompts.py` — Leave the CROWN vs INVERT DISTINCTION prompt text as-is (doesn't hurt, might help marginally)
- `src/extraction/run_hybrid.py` — No changes to sanitizer
- `src/extraction/schemas.py` — No schema changes
- `src/graph/merge.py` — No merge changes
- `src/report/html_report.py` — No report changes (crown_contamination findings render fine as-is)

---

## Success Criteria

1. Corridor SD `slope_mismatch` warnings: 4 → 0
2. New `crown_contamination` info findings appear in place of the false slope warnings
3. FNC Farms warnings: ≤16 (no regression from current 16)
4. All unit tests pass (`pytest tests/ -v`), including the 5 new crown-detection tests
5. Crown filter is NOT applied to W (water) utility graphs
