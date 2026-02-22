# Codex Task 004: Gravity Edge Orientation, Offset-Based Invert Fallback, and Proximity Structure Merge

## Context

After Task 003's pipe dedup and directional invert changes, the SD graph is nearly clean (0 errors, 5 findings). However, the SS graph has **13 findings** including 4 `flow_direction_error` and 4 `slope_mismatch`, all caused by two systematic root-cause bugs. A third minor bug causes one SD `slope_mismatch` false positive.

### Root Causes

**Bug A — Same-station invert fallback missing (SD):**
Edge `SD:e1:p14_r0_c1` connects INLET at 13+40.73/+45.00 RT to SDMH at 13+40.73/+28.00 RT (17 LF pipe, labeled slope 0.0059). Both structures are at the **same station**. The `_get_directional_invert()` function in `checks.py` uses relative station position (E/W) to pick the right invert, but when stations are equal it gets `preferred_dirs = None` and falls back to `representative_invert` (300.80 for both) → calculated slope = 0.0000 → false `slope_mismatch`.

The correct invert pair is INLET N=301.00 → SDMH S=300.90 → delta=0.10 → slope = 0.10/17 = 0.0059 (exact match). The fix is an **offset-based direction fallback** when stations are equal.

**Bug B — Edge direction wrong for gravity SS pipes:**
All 4 SS edges on page 36 flow from lower station → higher station, but inverts clearly increase with station:
- 10+05.68: inv 291.18
- 12+10.31: inv 294.38 (+3.20')
- 14+14.94: inv 294.94 (+0.56')

So actual gravity flow is high-station → low-station, but all edges point the wrong way. Every `flow_direction_error` is correct (the edge IS backwards). Fix: a **post-assembly gravity orientation pass** that flips edges where downstream invert > upstream invert.

**Bug C — Duplicate structures from plan/profile station discrepancy (SS):**
Page 36 has the same physical manholes extracted from overlapping plan-view and profile-view tiles with slightly different station readings:

| Physical MH | Plan tile station | Profile tile station | Delta |
|---|---|---|---|
| MH near 10+06 | 10+06.00 | 10+05.68 | 0.32' |
| MH near 12+08 | 12+07.59 | 12+10.31 | 2.72' |

Since exact dedup keys on `(page_number, stype, round(station_ft, 2), round(offset_ft, 2))`, these create **duplicate node pairs** instead of merging. Result: 7 structure nodes where there should be ~4, and pipes from plan/profile tiles connect to different copies of the same physical MH. This also blocks pipe dedup (the duplicate pipes connect to different node pairs). Fix: a **proximity merge pass** after exact dedup.

---

## Fix 1: Offset-Based Direction Fallback in `_get_directional_invert()`

**File:** `src/graph/checks.py`, function `_get_directional_invert()` (line 44)

**Current behavior:** When `my_station == other_station`, `preferred_dirs` is `None` → falls through to `representative_invert`.

**New behavior:** When stations are equal (within 0.5'), use signed offsets to determine preferred directions:
- If other node has **larger** offset (farther from CL): preferred_dirs = `{"N", "NE", "NW"}`
- If other node has **smaller** offset (closer to CL): preferred_dirs = `{"S", "SE", "SW"}`

Insert this block after the existing station-based direction logic (after line 64), before the `norm_size` line:

```python
    # Fallback: when stations are equal, use offset-based direction
    if preferred_dirs is None:
        my_offset = node_data.get("signed_offset_ft")
        other_offset = other_node_data.get("signed_offset_ft")
        if isinstance(my_offset, (int, float)) and isinstance(other_offset, (int, float)):
            if float(other_offset) > float(my_offset):
                preferred_dirs = {"N", "NE", "NW"}
            elif float(other_offset) < float(my_offset):
                preferred_dirs = {"S", "SE", "SW"}
```

**Why N/S not E/W:** When two structures share a station but differ in offset, the pipe between them runs perpendicular to the alignment. On typical plan views where north is up and station increases left-to-right, the perpendicular direction maps to N (away from CL) and S (toward CL). This is a heuristic — it works for the vast majority of California subdivision plans.

**Expected result:** SD edge `e1` slope check should pass (labeled 0.0059, calculated 0.0059).

---

## Fix 2: Gravity Edge Orientation Pass

**File:** `src/graph/assembly.py`

Add a new function `_orient_gravity_edges()` and call it in `build_utility_graph()` after `_deduplicate_pipe_edges(graph)` (line 526).

```python
def _orient_gravity_edges(graph: nx.DiGraph) -> None:
    """For SD/SS gravity systems, flip edges so from_node has higher invert (upstream)."""
    utility = str(graph.graph.get("utility_type", "")).upper()
    if utility not in {"SD", "SS"}:
        return

    edges_to_flip: list[tuple[str, str, dict[str, Any]]] = []
    for u, v, data in graph.edges(data=True):
        u_data = graph.nodes[u]
        v_data = graph.nodes[v]
        # Only flip between two resolved structure nodes
        if u_data.get("kind") != "structure" or v_data.get("kind") != "structure":
            continue

        u_inv = u_data.get("representative_invert")
        v_inv = v_data.get("representative_invert")
        if not isinstance(u_inv, (int, float)) or not isinstance(v_inv, (int, float)):
            continue

        # If from_node invert < to_node invert, edge is pointing uphill → flip
        if float(u_inv) < float(v_inv) - 0.01:  # 0.01' tolerance
            edges_to_flip.append((u, v, dict(data)))

    for u, v, data in edges_to_flip:
        graph.remove_edge(u, v)
        # Swap from/to metadata
        data["from_station"], data["to_station"] = data.get("to_station"), data.get("from_station")
        data["from_structure_hint"], data["to_structure_hint"] = (
            data.get("to_structure_hint"),
            data.get("from_structure_hint"),
        )
        data["from_match_confidence"], data["to_match_confidence"] = (
            data.get("to_match_confidence"),
            data.get("from_match_confidence"),
        )
        graph.add_edge(v, u, **data)
```

**Call site in `build_utility_graph()`:** After line 526:
```python
    _deduplicate_pipe_edges(graph)
    _orient_gravity_edges(graph)   # <-- add this line
```

**Important:** This must run AFTER pipe dedup (so we don't flip a duplicate that will be removed) and AFTER the proximity merge (Fix 3 below) has collapsed duplicate nodes (so flipping can see the correct inverts from merged nodes).

**Expected result:** All 4 SS `flow_direction_error` findings should disappear. Edges now flow from higher invert to lower invert.

---

## Fix 3: Proximity Structure Merge

**File:** `src/graph/merge.py`

Add a second-pass merge function that collapses nearby structures after exact dedup. This handles plan-vs-profile station discrepancies where the same physical manhole is extracted with slightly different station values.

### Approach

Add `_proximity_merge()` at the end of `merge_structures()`, after the exact dedup loop produces the `merged` list but before the final sort and return.

**Parameters:**
- `station_tol: float = 5.0` — maximum station difference in feet to consider merging
- `offset_tol: float = 1.0` — maximum signed offset difference in feet

**Merge criteria** (all must be true):
1. Same `page_number`
2. Same `structure_type` (normalized)
3. `abs(station_a - station_b) <= station_tol`
4. `abs(offset_a - offset_b) <= offset_tol` (if both offsets are non-None)
5. Both have non-None `parsed_station`

**When merging two MergedStructure records:**
- Pick the one with more inverts as the "primary" (same `_structure_rank` logic)
- Combine `source_tile_ids`, `source_page_numbers`, `source_text_ids`
- Combine `rim_elevation_values`
- Sum `variants_count`
- Use the primary's station/offset/inverts/notes/elevations
- Regenerate `node_id` from the primary's attributes
- Set `sanitized = any_sanitized`

**Implementation sketch:**

```python
def _proximity_merge(
    merged: list[MergedStructure],
    station_tol: float = 5.0,
    offset_tol: float = 1.0,
) -> list[MergedStructure]:
    """Second-pass: collapse structures that are likely the same physical MH."""
    if len(merged) <= 1:
        return merged

    # Sort by (page, type, station) for efficient neighbor scanning
    items = sorted(
        merged,
        key=lambda m: (
            m.page_number,
            m.structure_type.upper(),
            m.parsed_station if m.parsed_station is not None else float("inf"),
        ),
    )

    used: set[int] = set()
    result: list[MergedStructure] = []

    for i, item in enumerate(items):
        if i in used:
            continue
        group = [item]
        for j in range(i + 1, len(items)):
            if j in used:
                continue
            other = items[j]
            if other.page_number != item.page_number:
                break
            if other.structure_type.upper() != item.structure_type.upper():
                continue
            if item.parsed_station is None or other.parsed_station is None:
                continue
            if abs(item.parsed_station - other.parsed_station) > station_tol:
                break  # sorted by station, so no more matches
            if (
                item.signed_offset is not None
                and other.signed_offset is not None
                and abs(item.signed_offset - other.signed_offset) > offset_tol
            ):
                continue
            group.append(other)
            used.add(j)

        if len(group) == 1:
            result.append(item)
        else:
            result.append(_collapse_merged_group(group, item.page_number))

    return result
```

**`_collapse_merged_group()` helper:** Takes a list of `MergedStructure` and produces a single merged one. Pick the record with the most inverts as primary. Combine provenance lists. This is similar to the existing exact-merge logic but operates on `MergedStructure` objects instead of raw `Structure` records.

```python
def _collapse_merged_group(
    group: list[MergedStructure],
    page_number: int,
) -> MergedStructure:
    """Collapse multiple MergedStructure records into one."""
    # Pick primary: most inverts, then longest notes, then most source_text_ids
    primary = max(group, key=lambda m: (len(m.inverts), len(m.notes or ""), len(m.source_text_ids)))

    all_tile_ids = sorted({tid for m in group for tid in m.source_tile_ids})
    all_page_numbers = sorted({p for m in group for p in m.source_page_numbers})
    all_text_ids = sorted({t for m in group for t in m.source_text_ids})
    all_rim_values = sorted({r for m in group for r in m.rim_elevation_values})
    total_variants = sum(m.variants_count for m in group)
    any_sanitized = any(m.sanitized for m in group)

    return MergedStructure(
        node_id=primary.node_id,
        page_number=page_number,
        structure_type=primary.structure_type,
        station=primary.station,
        offset=primary.offset,
        parsed_station=primary.parsed_station,
        signed_offset=primary.signed_offset,
        id=primary.id or next((m.id for m in group if m.id), None),
        size=primary.size or next((m.size for m in group if m.size), None),
        rim_elevation=primary.rim_elevation or next(
            (m.rim_elevation for m in group if m.rim_elevation is not None), None
        ),
        tc_elevation=primary.tc_elevation or next(
            (m.tc_elevation for m in group if m.tc_elevation is not None), None
        ),
        fl_elevation=primary.fl_elevation or next(
            (m.fl_elevation for m in group if m.fl_elevation is not None), None
        ),
        inverts=primary.inverts,
        notes=max(
            (m.notes for m in group),
            key=lambda n: len((n or "").strip()),
            default=None,
        ),
        source_tile_ids=all_tile_ids,
        source_page_numbers=all_page_numbers,
        source_text_ids=all_text_ids,
        sanitized=any_sanitized,
        variants_count=total_variants,
        rim_elevation_values=all_rim_values,
    )
```

**Call site in `merge_structures()`:** Before the final `return sorted(merged, ...)`, insert:
```python
    merged = _proximity_merge(merged)
```

---

## Execution Order

The three fixes interact and must be applied in this order:

1. **Fix 3 (proximity merge)** — runs first in the pipeline (during `merge_structures()`)
   - Collapses duplicate SSMH nodes → pipes from different tiles now connect to the same node
   - This enables pipe dedup to catch the previously-missed duplicates
2. **Fix 2 (gravity orientation)** — runs after pipe dedup in `build_utility_graph()`
   - Flips edges where from_invert < to_invert for SD/SS
   - Must run after proximity merge so merged nodes have the correct inverts
3. **Fix 1 (offset fallback)** — runs at check time in `_get_directional_invert()`
   - Only affects same-station pairs where the station heuristic produces no direction

---

## Tests Required

### `tests/test_graph_checks.py` — add:

**`test_directional_invert_offset_fallback`:**
```python
def test_directional_invert_offset_fallback(self) -> None:
    """When two structures share a station, use offset to pick invert direction."""
    graph = nx.DiGraph(utility_type="SD")
    graph.add_node(
        "inlet",
        kind="structure",
        station_ft=1340.73,
        signed_offset_ft=45.0,
        representative_invert=300.8,
        inverts=[
            {"direction": "N", "pipe_size": '12"', "elevation": 301.0},
            {"direction": "E", "pipe_size": '12"', "elevation": 300.8},
            {"direction": "S", "pipe_size": '12"', "elevation": 300.9},
        ],
        source_page_numbers=[14],
        source_text_ids=[1],
    )
    graph.add_node(
        "sdmh",
        kind="structure",
        station_ft=1340.73,
        signed_offset_ft=28.0,
        representative_invert=300.8,
        inverts=[
            {"direction": "E", "pipe_size": '12"', "elevation": 300.8},
            {"direction": "S", "pipe_size": '12"', "elevation": 300.9},
        ],
        source_page_numbers=[14],
        source_text_ids=[2],
    )
    # Pipe from inlet (+45 offset) to sdmh (+28 offset), 17 LF, labeled slope 0.0059
    # Correct inverts: inlet S=300.9 (toward lower offset) -> sdmh ... representative 300.8
    # delta=0.10, slope = 0.10/17 = 0.00588 ≈ 0.0059
    graph.add_edge(
        "inlet", "sdmh",
        edge_id="e1",
        size='12"',
        length_lf=17.0,
        slope=0.0059,
        source_page_numbers=[14],
        source_text_ids=[3],
    )

    findings = check_slope_consistency(graph)
    # Should NOT produce a slope_mismatch — offset fallback should pick correct inverts
    slope_findings = [f for f in findings if f.finding_type == "slope_mismatch"]
    self.assertEqual(len(slope_findings), 0)
```

### `tests/test_graph_assembly.py` — add:

**`test_gravity_orientation_flips_uphill_edge`:**
```python
def test_gravity_orientation_flips_uphill_edge(self) -> None:
    """SS edge from low-invert node to high-invert node should be flipped."""
    ext = _extraction({
        "tile_id": "p36_r1_c1",
        "page_number": 36,
        "sheet_type": "plan_view",
        "utility_types_present": ["SS"],
        "structures": [
            {
                "id": "A",
                "structure_type": "SSMH",
                "station": "10+00.00",
                "offset": "6.00' RT",
                "inverts": [{"direction": "E", "pipe_size": '8"', "pipe_type": "SS",
                             "elevation": 291.0, "source_text_ids": [1]}],
                "source_text_ids": [10],
            },
            {
                "id": "B",
                "structure_type": "SSMH",
                "station": "12+00.00",
                "offset": "6.00' RT",
                "inverts": [{"direction": "W", "pipe_size": '8"', "pipe_type": "SS",
                             "elevation": 294.0, "source_text_ids": [2]}],
                "source_text_ids": [20],
            },
        ],
        "pipes": [{
            "pipe_type": "SS",
            "size": '8"',
            "length_lf": 200.0,
            "slope": 0.015,
            "from_station": "10+00.00",
            "to_station": "12+00.00",
            "from_structure_hint": "A",
            "to_structure_hint": "B",
            "source_text_ids": [30],
        }],
        "callouts": [],
        "street_names": [],
        "lot_numbers": [],
    })

    graph = build_utility_graph(extractions=[ext], utility_type="SS", tile_meta_by_id={})
    # Edge should be flipped: B (inv 294.0) -> A (inv 291.0), not A -> B
    edges = list(graph.edges(data=True))
    self.assertEqual(len(edges), 1)
    from_node, to_node, data = edges[0]
    from_inv = graph.nodes[from_node].get("representative_invert")
    to_inv = graph.nodes[to_node].get("representative_invert")
    self.assertGreater(from_inv, to_inv, "Edge should flow from higher invert to lower invert")
```

**`test_gravity_orientation_preserves_correct_direction`:**
```python
def test_gravity_orientation_preserves_correct_direction(self) -> None:
    """SS edge already pointing downhill should not be flipped."""
    ext = _extraction({
        "tile_id": "p36_r1_c1",
        "page_number": 36,
        "sheet_type": "plan_view",
        "utility_types_present": ["SS"],
        "structures": [
            {
                "id": "UP",
                "structure_type": "SSMH",
                "station": "14+00.00",
                "offset": "6.00' RT",
                "inverts": [{"direction": "W", "pipe_size": '8"', "pipe_type": "SS",
                             "elevation": 296.0, "source_text_ids": [1]}],
                "source_text_ids": [10],
            },
            {
                "id": "DN",
                "structure_type": "SSMH",
                "station": "12+00.00",
                "offset": "6.00' RT",
                "inverts": [{"direction": "E", "pipe_size": '8"', "pipe_type": "SS",
                             "elevation": 294.0, "source_text_ids": [2]}],
                "source_text_ids": [20],
            },
        ],
        "pipes": [{
            "pipe_type": "SS",
            "size": '8"',
            "length_lf": 200.0,
            "slope": 0.010,
            "from_station": "14+00.00",
            "to_station": "12+00.00",
            "from_structure_hint": "UP",
            "to_structure_hint": "DN",
            "source_text_ids": [30],
        }],
        "callouts": [],
        "street_names": [],
        "lot_numbers": [],
    })

    graph = build_utility_graph(extractions=[ext], utility_type="SS", tile_meta_by_id={})
    edges = list(graph.edges(data=True))
    self.assertEqual(len(edges), 1)
    from_node, to_node, data = edges[0]
    from_inv = graph.nodes[from_node].get("representative_invert")
    to_inv = graph.nodes[to_node].get("representative_invert")
    self.assertGreaterEqual(from_inv, to_inv, "Already-correct edge should not be flipped")
```

### `tests/test_graph_merge.py` — add:

**`test_proximity_merge_collapses_nearby_structures`:**
```python
def test_proximity_merge_collapses_nearby_structures(self) -> None:
    """Structures at 10+05.68 and 10+06.00 with same offset should merge."""
    plan_view = _extraction({
        "tile_id": "p36_r1_c0",
        "page_number": 36,
        "sheet_type": "plan_view",
        "utility_types_present": ["SS"],
        "structures": [{
            "structure_type": "SSMH",
            "station": "10+06.00",
            "offset": "6.00' RT",
            "rim_elevation": 301.76,
            "inverts": [
                {"direction": "E", "pipe_size": '8"', "pipe_type": "SS",
                 "elevation": 294.46, "source_text_ids": [61]},
            ],
            "source_text_ids": [60],
        }],
        "pipes": [],
        "callouts": [],
        "street_names": [],
        "lot_numbers": [],
    })
    profile_view = _extraction({
        "tile_id": "p36_r1_c1",
        "page_number": 36,
        "sheet_type": "profile_view",
        "utility_types_present": ["SS"],
        "structures": [{
            "structure_type": "SSMH",
            "station": "10+05.68",
            "offset": "6.00' RT",
            "rim_elevation": 300.89,
            "inverts": [
                {"direction": "E", "pipe_size": '8"', "pipe_type": "SS",
                 "elevation": 291.18, "source_text_ids": [70]},
                {"direction": "W", "pipe_size": '8"', "pipe_type": "SS",
                 "elevation": 291.18, "source_text_ids": [71]},
            ],
            "source_text_ids": [69],
        }],
        "pipes": [],
        "callouts": [],
        "street_names": [],
        "lot_numbers": [],
    })

    merged = merge_structures(
        extractions=[plan_view, profile_view],
        utility_type="SS",
        tile_meta_by_id={},
    )
    # Should collapse to 1 structure (proximity merge)
    self.assertEqual(len(merged), 1)
    self.assertIn("p36_r1_c0", merged[0].source_tile_ids)
    self.assertIn("p36_r1_c1", merged[0].source_tile_ids)
    # Primary should be the one with more inverts (profile_view has 2)
    self.assertEqual(len(merged[0].inverts), 2)
```

**`test_proximity_merge_preserves_distinct_structures`:**
```python
def test_proximity_merge_preserves_distinct_structures(self) -> None:
    """Structures at 10+06.00 and 12+07.59 should NOT merge (too far apart)."""
    a = _extraction({
        "tile_id": "p36_r0_c0",
        "page_number": 36,
        "sheet_type": "plan_view",
        "utility_types_present": ["SS"],
        "structures": [{
            "structure_type": "SSMH",
            "station": "10+06.00",
            "offset": "6.00' RT",
            "source_text_ids": [1],
        }],
        "pipes": [],
        "callouts": [],
        "street_names": [],
        "lot_numbers": [],
    })
    b = _extraction({
        "tile_id": "p36_r0_c1",
        "page_number": 36,
        "sheet_type": "plan_view",
        "utility_types_present": ["SS"],
        "structures": [{
            "structure_type": "SSMH",
            "station": "12+07.59",
            "offset": "6.00' RT",
            "source_text_ids": [2],
        }],
        "pipes": [],
        "callouts": [],
        "street_names": [],
        "lot_numbers": [],
    })

    merged = merge_structures(extractions=[a, b], utility_type="SS", tile_meta_by_id={})
    self.assertEqual(len(merged), 2)
```

---

## Validation Checklist

After implementing all three fixes, run:

```bash
# Unit tests
python -m unittest discover -s tests -v
# Expected: all existing 16 + 5 new = 21 tests passing

# Rebuild graphs from calibration-clean extractions
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SD --out output/graphs/calibration-clean-sd.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type SS --out output/graphs/calibration-clean-ss.json
python -m src.graph.assembly --extractions-dir output/extractions/calibration-clean --utility-type W  --out output/graphs/calibration-clean-w.json

# Run checks and write findings
# (use existing CLI or the check runner from assembly main)
```

**Expected outcomes:**

| Metric | Before | After |
|---|---|---|
| SD findings (total) | 5 | 4 (slope_mismatch on e1 gone) |
| SD errors | 0 | 0 |
| SS structure nodes (page 36) | 6 | ~3-4 (duplicates merged) |
| SS flow_direction_error | 4 | 0 (edges oriented correctly) |
| SS slope_mismatch | 4 | <=2 (better inverts + fewer dup edges) |
| SS findings (total) | 13 | significantly reduced |
| W findings | 1 (info) | 1 (info, unchanged) |
| Unit tests | 16/16 | 21/21 |

**Key verification:** After gravity orientation, check that no SS edge has `from_node` with a lower `representative_invert` than `to_node`. Run this check in the validation:

```python
for u, v, data in graph.edges(data=True):
    u_inv = graph.nodes[u].get("representative_invert")
    v_inv = graph.nodes[v].get("representative_invert")
    if isinstance(u_inv, (int, float)) and isinstance(v_inv, (int, float)):
        assert float(u_inv) >= float(v_inv) - 0.01, f"Edge {data.get('edge_id')} still uphill: {u_inv} < {v_inv}"
```

---

## Files Modified

| File | Changes |
|---|---|
| `src/graph/checks.py` | Add offset-based fallback in `_get_directional_invert()` (~8 lines) |
| `src/graph/assembly.py` | Add `_orient_gravity_edges()` function + call after dedup (~30 lines) |
| `src/graph/merge.py` | Add `_proximity_merge()`, `_collapse_merged_group()` + call in `merge_structures()` (~60 lines) |
| `tests/test_graph_checks.py` | Add `test_directional_invert_offset_fallback` |
| `tests/test_graph_assembly.py` | Add 2 gravity orientation tests |
| `tests/test_graph_merge.py` | Add 2 proximity merge tests |

No schema changes. No extraction changes. No new dependencies.
