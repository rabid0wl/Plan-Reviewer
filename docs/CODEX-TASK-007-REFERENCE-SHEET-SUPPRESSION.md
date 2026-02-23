# Codex Task 007: Suppress Connectivity Findings from Reference-Only Sheets

## Context

Signing/Striping/Markings (SSM) plan sheets show **existing utilities as a background reference layer**, not as design elements. The model correctly identifies these utilities, but they carry no station, offset, length, slope, or reliable endpoint data — because SSM sheets don't annotate those fields for reference callouts.

The sanitizer correctly drops SSM structures (no station → dropped). But **pipes survive sanitization** because they only require `pipe_type` and `size`. These surviving pipes then flow into `build_utility_graph()`, which tries to connect them to structures and fails, producing false positive findings.

**Evidence from corridor-expanded run (pages 24–26, 93, 103):**

In the SD graph:
- `SD:e0:p103_r1_c1` — dead_end_pipe warning (from page 103, SSM sheet)
- `SD:e1:p103_r1_c1` — unanchored_pipe info (page 103)
- `SD:e2:p103_r1_c2` — unanchored_pipe info (page 103)

In the SS graph:
- `SS:e0:p103_r0_c0` — dead_end_pipe — SSM pipe cross-matched to a College Ave SSMH on page 24 (wrong corridor!)
- `SS:e5:p103_r1_c1` — dead_end_pipe — SSM pipe cross-matched to page 26 structures
- 16 unanchored_pipe findings — all from pages 93 and 103 (SSM sheets)

**Root cause:** When a pipe from an SSM tile has a `to_structure_hint` (e.g., `"SSMHRIM 334.00"`), `_best_node_match()` searches `all_candidates` across all pages and finds the closest structure — even if it's on a completely different corridor. This is the cross-corridor false match (Option B from the diagnosis). Both Option A and Option B are symptoms of the same root cause: SSM pipe edges should not participate in connectivity checks.

**Scope:** This task adds an `is_reference_only` flag to pipe edges sourced from signing_striping tiles, then suppresses connectivity findings for those edges. It does NOT fix other findings types (slope, crown, elevation) — those would apply if SSM sheets somehow had that data.

---

## Evidence: Finding Breakdown

After this fix, expected changes:

| Finding | Before | After | Notes |
|---|---|---|---|
| SS dead_end_pipe | 7 | 4 | 3 from pages 93/103 suppressed; 4 from page 24 remain (legitimate) |
| SS unanchored_pipe | 16 | 0 | All from pages 93/103 |
| SD dead_end_pipe | 1 | 0 | From page 103 |
| SD unanchored_pipe | 2 | 0 | From page 103 |
| SS/SD total | 30 | 7 | Crown/slope/orphan_suppressed findings unaffected |

---

## Implementation

### Part 1: Tag Reference Edges — `src/graph/assembly.py`

**Step 1a: Add a module-level constant** near the top of the file (after `_CONFIDENCE_ORDER`):

```python
_REFERENCE_SHEET_TYPES: frozenset[str] = frozenset({"signing_striping"})
```

**Step 1b: Tag the edge at creation time** in `build_utility_graph()`.

Current `graph.add_edge(...)` call (around line 656):
```python
graph.add_edge(
    from_node,
    to_node,
    edge_id=edge_id,
    ...
    sanitized=tile_sanitized,
)
```

Add one field:
```python
graph.add_edge(
    from_node,
    to_node,
    edge_id=edge_id,
    ...
    sanitized=tile_sanitized,
    is_reference_only=extraction.sheet_type in _REFERENCE_SHEET_TYPES,
)
```

**Step 1c: Propagate through deduplication** in `_merge_edge_provenance()`.

The existing function already propagates `crown_contamination_candidate` with OR logic. Add the same pattern for `is_reference_only`:

```python
kept["is_reference_only"] = bool(
    kept.get("is_reference_only", False)
    or dropped.get("is_reference_only", False)
)
```

Add this line immediately after the `crown_contamination_candidate` block.

---

### Part 2: Suppress Connectivity Findings — `src/graph/checks.py`

Modify `check_connectivity()`. In the edge loop (currently around line 330), add an early-continue guard at the top of the loop body, before the existing `unresolved` check:

```python
for u, v, data in graph.edges(data=True):
    # Skip reference-only edges (e.g., from signing/striping sheets)
    if data.get("is_reference_only"):
        continue

    unresolved = (
        data.get("matched_confidence") == "none"
        or graph.nodes[u].get("kind") == "orphan_anchor"
        or graph.nodes[v].get("kind") == "orphan_anchor"
    )
    ...
```

This single guard suppresses both `unanchored_pipe` and `dead_end_pipe` findings for reference edges.

---

### Part 3: Unit Tests — `tests/test_graph_checks.py`

Add two test cases to the existing `GraphChecksTests` class:

**Test 1: Reference edge suppresses unanchored_pipe finding**

Build a graph with a pipe edge that has `is_reference_only=True`, `matched_confidence="none"`, and two orphan_anchor endpoints. Run `check_connectivity(graph)`. Assert no `unanchored_pipe` finding is produced.

```python
def test_reference_only_edge_suppresses_unanchored(self) -> None:
    graph = nx.DiGraph(utility_type="SS")
    graph.add_node("a1", kind="orphan_anchor")
    graph.add_node("a2", kind="orphan_anchor")
    graph.add_edge(
        "a1", "a2",
        edge_id="ref_edge",
        matched_confidence="none",
        is_reference_only=True,
        source_page_numbers=[103],
        source_text_ids=[],
    )

    findings = check_connectivity(graph)
    finding_types = {f.finding_type for f in findings}
    self.assertNotIn("unanchored_pipe", finding_types)
    self.assertNotIn("dead_end_pipe", finding_types)
```

**Test 2: Non-reference edge still generates unanchored_pipe**

Same graph as Test 1, but with `is_reference_only=False`. Assert that `unanchored_pipe` IS produced (existing behavior preserved).

```python
def test_non_reference_edge_still_flags_unanchored(self) -> None:
    graph = nx.DiGraph(utility_type="SS")
    graph.add_node("s1", kind="structure", source_page_numbers=[24], source_text_ids=[1])
    graph.add_node("a1", kind="orphan_anchor")
    graph.add_edge(
        "s1", "a1",
        edge_id="real_edge",
        matched_confidence="none",
        is_reference_only=False,
        source_page_numbers=[24],
        source_text_ids=[2],
    )

    findings = check_connectivity(graph)
    finding_types = {f.finding_type for f in findings}
    self.assertIn("unanchored_pipe", finding_types)
```

---

## Validation

### Step 1: Run unit tests

```bash
python -m pytest tests/test_graph_checks.py -v
python -m pytest tests/ -v
```

All existing tests must pass. The 2 new tests must pass.

### Step 2: Regenerate corridor-expanded findings and report

Existing extractions are at `output/extractions/corridor-expanded/`. Regenerate downstream artifacts only (no re-tiling or re-extraction needed):

```bash
python -c "
import json
from pathlib import Path
from src.graph.assembly import build_utility_graph, load_extractions_with_meta
from src.graph.checks import run_all_checks

extractions_dir = Path('output/extractions/corridor-expanded')
findings_dir = Path('output/graphs/findings')
findings_dir.mkdir(parents=True, exist_ok=True)
prefix = 'corridor-expanded'

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
```

Then regenerate the HTML report:

```bash
python -m src.report.html_report \
  --graphs-dir output/graphs \
  --findings-dir output/graphs/findings \
  --prefix corridor-expanded \
  --batch-summary output/extractions/corridor-expanded/batch_summary.json \
  --out output/reports/corridor-expanded-report.html
```

### Step 3: Regression check on FNC Farms

FNC Farms has no signing_striping sheets, so `is_reference_only` should never be set True. Re-run findings to confirm no regression:

```bash
python -c "
import json
from pathlib import Path
from src.graph.assembly import build_utility_graph, load_extractions_with_meta
from src.graph.checks import run_all_checks

extractions_dir = Path('output/extractions/calibration-postfix')
findings_dir = Path('output/graphs/findings')
findings_dir.mkdir(parents=True, exist_ok=True)
prefix = 'calibration-postfix'

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
```

**Regression threshold:** FNC Farms warning count must not increase from baseline.

---

## Files to Modify

1. **`src/graph/assembly.py`** — Add `_REFERENCE_SHEET_TYPES` constant; set `is_reference_only` on edges; propagate in `_merge_edge_provenance()`
2. **`src/graph/checks.py`** — Skip `is_reference_only` edges in `check_connectivity()` edge loop
3. **`tests/test_graph_checks.py`** — Add 2 new test cases

## Files NOT to Modify

- `src/extraction/run_hybrid.py` — Sanitizer station-drop logic is correct; do not relax it
- `src/extraction/schemas.py` — `sheet_type` field already exists and already has `"signing_striping"` as a valid value
- `src/graph/merge.py` — No merge changes needed
- `src/report/html_report.py` — Report renders existing finding types fine; reference edges don't appear in findings after this fix

---

## Success Criteria

1. SS `dead_end_pipe` warnings: 7 → 4 (3 from pages 93/103 suppressed)
2. SS `unanchored_pipe` info: 16 → 0
3. SD `dead_end_pipe` warnings: 1 → 0 (page 103)
4. SD `unanchored_pipe` info: 2 → 0 (page 103)
5. All 2 new unit tests pass; all existing tests continue to pass
6. FNC Farms warning count does not increase (no regression)
7. `is_reference_only` is NOT set on any FNC Farms edges (confirm via: no edges in FNC graphs have the flag set True)
