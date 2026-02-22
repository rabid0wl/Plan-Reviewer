# Civil Engineering Plan Review Tool — Architecture & Project Summary

**Last updated:** 2026-02-21
**Author:** Dylan (PE #98682), 4Creeks Engineering
**Status:** Phase 1 complete, building extraction pipeline

**Key docs:** `PROGRESS.md` (working journal) · `docs/findings/` (test results)

---

## Repository Layout & Documentation Conventions

```
├── ARCHITECTURE.md          # This file — design blueprint
├── PROGRESS.md              # Working journal (decisions, learnings, costs)
├── plan_reviewer.py         # Existing Streamlit prototype (pre-rebuild)
├── docs/findings/           # Polished investigation results
├── test-extractions/        # PNG tiles from vision testing
├── References/              # Source PDFs, CrossBeam reference impl
├── skills/                  # (planned) Agency standards, extraction schemas
└── src/                     # (planned) Agent SDK pipeline code
```

**Documentation philosophy — three docs, clear purposes, no overlap:**

- **`ARCHITECTURE.md`** (this file) — The design blueprint. What we're building, why, and how the pieces fit together. Update when the design changes.
- **`PROGRESS.md`** — The working journal. Grouped by component (Vision, Tiling, Skills, etc.) with dated entries, not chronological. Contains a Decisions Log table at the top for quick reference, open questions, cost tracking, and session summaries. High verbosity during active work, pruned at session end. **If you're unsure where to put something, it goes here.**
- **`docs/findings/`** — Polished reference docs for completed investigations (e.g., Phase 1 vision validation). Only created when a topic outgrows PROGRESS.md and will be referenced repeatedly. Linked from PROGRESS.md, not duplicated.

Don't create new top-level markdown files. Everything goes in PROGRESS.md until it earns its own doc in `docs/findings/`.

---

## Vision

An AI-powered plan review tool that catches consistency errors, cross-reference mismatches, and standards compliance issues in civil engineering plan sets before they become RFIs. Starts as a personal tool, long-term goal is a deployable web product for firms.

## Problem Statement

Plan review is tedious and error-prone. The bulk of the work is:
- **Consistency checking (~60-70% of effort):** Does a material callout on Sheet C3 match what's shown on Sheet U2 and the detail on Sheet D4? Do detail bubble references point to the correct detail sheet? Are inverts consistent between plan and profile views?
- **Standards compliance (~30-40%):** Are pipe sizes correct per agency design tables? Are minimum slopes met? Cover depths adequate? Manhole spacing per standards?

Mistakes here generate RFIs during construction, costing real money and time. Most of this checking is mechanical — the kind of thing an agent should be able to do.

## Key Insight: Two Tools, Not One

### Tool 1: Consistency Checker (build first, highest ROI)
- Extracts structured data from every sheet via vision (callouts, materials, sizes, elevations, detail references, sheet references)
- Builds a cross-reference database keyed by location (station, grid ref)
- Diffs the database against itself looking for conflicts
- Flags mismatches with sheet/location citations
- **Does NOT need regulatory skills** — pure extraction + pattern matching
- This is where 80% of RFI-prevention value lives

### Tool 2: Standards Compliance Reviewer (build incrementally)
- Loads agency-specific standards as skills
- Checks pipe sizes against design tables
- Verifies minimum slopes, cover depths, manhole spacing, etc.
- Skills grow organically per agency (add Visalia skill when reviewing Visalia project)
- This is where domain knowledge complexity lives

## Reference Implementation: CrossBeam (cc-crossbeam)

CrossBeam is an ADU permit review tool built during the Anthropic Claude Code Hackathon (Feb 10-16, 2026) by Mike Brown. Full repo is in `References/cc-crossbeam-main/`. It solves a different problem (responding to city correction letters for ADU permits) but independently arrived at nearly identical architecture patterns.

### What Transfers Directly

**Skills architecture:** Decomposed 54-page HCD ADU Handbook into 28 focused reference files with decision-tree SKILL.md router. YAML frontmatter with `relevance:` fields so agent only loads 3-5 of 28 files per query. We use the same pattern for CPC, OWTS Policy, Caltrans standards, agency design standards.

**PDF → PNG → Vision pipeline:** `pdftoppm` for extraction, ImageMagick for processing. Critical constraint discovered: Claude API caps images at 2,000px when >20 images in a batch, but civil plans are 7,400px wide. Solution: one page per subagent, rolling window of 3 concurrent subagents.

**Sheet manifest:** Maps sheet labels (C1, G2, U1, SS1, W1) to page numbers and content descriptions. Our existing prototype already has `SHEET_CATEGORIES` doing basic classification.

**Test ladder (L0→L4):** Cheapest models for smoke tests, expensive models only for full pipeline. Saves enormous money during development. His total test ladder for city flow was $9.80 (budgeted $18-30).

**Agent SDK config pattern:** `config.ts`, `session.ts`, `progress.ts`, `verify.ts` scaffolding. Winning config with `settingSources: ['project']` for skill discovery, `bypassPermissions`, specific `allowedTools` list, `maxBudgetUsd` caps.

**File-based handoffs:** Subagents write JSON outputs, next phase reads them cold with no conversation history. Enables independent testing of each phase.

**Deployment stack:** Next.js on Vercel (frontend), Cloud Run on GCP (orchestrator, needed because agent runs take 10-20 min and serverless times out), Vercel Sandbox (Agent SDK execution), Supabase (state, realtime updates, storage).

### What Does NOT Transfer

**Targeted viewing pivot:** CrossBeam reads correction letters first to know what to look at. Plan review IS the finding — we can't pre-filter. Need a two-pass approach instead (fast triage scan → targeted deep dives).

**Jurisdiction research skill (3-mode web search):** Civil engineering review uses stable project specs and agency standards, not 480 different city websites. Our "jurisdiction" is project-level special provisions loaded as skills.

**Engineering judgment:** ADU permit review is largely checklist-based. Civil plan review requires spatial reasoning (pipe cover under proposed road), hydraulic adequacy, constructability assessment. Agent flags issues; PE makes judgment calls.

### Key CrossBeam Documents to Study

| File | Content | Lines |
|------|---------|-------|
| `docs/learnings-agents-sdk.md` | 13 bugs + fixes, test ladder, cost benchmarks, 11 principles | 401 |
| `docs/plans/plan-skill-aduHandbook.md` | Skill decomposition methodology | 282 |
| `docs/plans/plan-contractors-agents-sdk.md` | Corrections flow architecture | 1077 |
| `docs/plans/plan-city-agents-sdk.md` | City review flow architecture | — |
| `CC-PROGRESS.md` | 7-day hackathon journal with real decisions and pivots | 451 |
| `agents-crossbeam/src/utils/config.ts` | Proven Agent SDK configuration | — |
| `agents-crossbeam/src/utils/verify.ts` | Post-run file verification patterns | — |

## Proposed Architecture

### Layer 1: Domain Skills (Phase 0 — zero API cost)

Build using Pro subscription in claude.ai / Claude Code. Immediately useful for project work.

```
skills/
├── civil-standards/
│   ├── SKILL.md                    # Decision tree router
│   ├── AGENTS.md                   # Agent catalog
│   └── references/
│       ├── cpc-plumbing.md         # California Plumbing Code excerpts
│       ├── cpc-mechanical.md
│       ├── cbc-structural.md
│       ├── caltrans-standard-plans.md
│       ├── aashto-geometric.md
│       └── ...
├── owts-policy/
│   ├── SKILL.md
│   └── references/
│       ├── tulare-county-owts.md
│       ├── rwqcb-requirements.md
│       └── ...
├── agency-standards/
│   ├── city-of-visalia/
│   │   ├── SKILL.md
│   │   └── references/
│   │       ├── improvement-standards.md
│   │       ├── standard-details.md
│   │       └── design-criteria.md
│   ├── tulare-county/
│   ├── city-of-dinuba/
│   └── ... (add per agency as needed)
├── plan-conventions/
│   ├── SKILL.md
│   └── references/
│       ├── sheet-naming.md         # C, G, U, SS, W, D, E, S conventions
│       ├── title-block-standards.md
│       ├── abbreviations.md
│       ├── line-types.md
│       └── detail-callout-conventions.md
└── extraction-schemas/
    ├── pipe-callout-schema.json
    ├── detail-reference-schema.json
    ├── material-spec-schema.json
    └── elevation-callout-schema.json
```

### Layer 2: Review Pipeline (Phase 2 — Agent SDK, ~$50-75 to develop)

```
Phase 1: INTAKE
├── PDF → PNG tile extraction (PyMuPDF, 3x2 grid, 300 DPI, 10% overlap)
├── PDF text layer extraction (PyMuPDF get_text("dict") — exact strings + bounding boxes)
├── Sheet manifest generation (cover sheet index first, then vision validates title blocks)
├── Project metadata capture (agency, project type, applicable standards)
└── Output: manifest.json, tiles/, text-layers/, project-config.json

Phase 2: TRIAGE SCAN (fast pass, cheap model)
├── Quick vision pass on each sheet (full page, low res — layout classification only)
├── Classify content type per sheet
├── Flag areas of interest for deep review
└── Output: review_priorities.json

Phase 3: DEEP EXTRACTION (one tile per subagent, rolling window of 3)
├── HYBRID approach per tile:
│   ├── Vision reads the tile image for spatial understanding (what connects to what)
│   ├── PDF text layer provides exact strings for all numbers (elevations, stations, sizes)
│   ├── Prompt: "Use the image for layout/context, but ALWAYS use the text-layer values
│   │          for elevations, stations, pipe sizes, and slopes."
│   └── Schema-driven JSON output per tile
├── Per-sheet structured extraction:
│   ├── All material callouts with station/location
│   ├── All detail references (bubble number → target sheet)
│   ├── All pipe sizes, slopes, inverts
│   ├── All elevation callouts
│   ├── All text annotations / notes
│   └── Sheet cross-references
├── Merge tiles → per-sheet extraction (deduplicate overlap zone)
└── Output: per-sheet extraction JSONs

Phase 3.5: GRAPH ASSEMBLY
├── Build utility network graph from per-sheet extractions:
│   ├── Nodes = structures (MH, CB, inlet, cleanout, etc.)
│   │   └── Attributes: ID, station, offset, rim, inverts[], source_sheet
│   ├── Edges = pipes
│   │   └── Attributes: start_node, end_node, size, material, length, slope, source_sheet
│   └── Cross-sheet node merging (same structure on multiple sheets → single node)
├── Separate graphs per utility: SD, SS, W (and combined overlay)
└── Output: utility_graph.json (nodes + edges + per-item source_sheet traceability)

Phase 4: CROSS-REFERENCE ANALYSIS
├── Deterministic checks on graph (Python, no LLM needed):
│   ├── Slope verification: (nodeA.invert - nodeB.invert) / edge.length == edge.slope?
│   ├── Pipe size consistency: same edge referenced on plan, profile, schedule → same size?
│   ├── Elevation consistency: same node on plan vs profile vs detail → same inverts?
│   ├── Connectivity: are all nodes reachable? Any orphan pipes?
│   └── Detail reference accuracy: does detail bubble D7/Sheet D2 actually exist?
├── LLM-assisted checks (need context/judgment):
│   ├── Material consistency across sheets (same location, different callout text)
│   ├── Spelling/naming consistency
│   └── Annotation conflict detection (contradictory notes)
├── Standards compliance checks (loads agency skills):
│   ├── Pipe sizing vs design tables
│   ├── Minimum slopes
│   ├── Cover depths
│   ├── Manhole spacing
│   └── Other deterministic checks
└── Output: findings.json (categorized by severity, each finding cites source sheets)

Phase 5: OUTPUT GENERATION
├── Review comment log (structured, exportable)
├── Flagged sheets with cropped screenshots of exact finding locations
├── Code/standard citations for each comment
├── Items needing PE judgment clearly marked
├── Triage-ready format (accept/reject per finding for PE review)
└── Output: review_report.md, findings.json, flagged_sheets/
```

### Layer 3: Deployment (Phase 3 — CrossBeam's exact stack)

```
┌─────────────────────────────────────────┐
│           Next.js on Vercel             │
│  ┌──────────────────────────────────┐   │
│  │  Frontend (React + Tailwind)     │   │
│  │  - Upload plan set               │   │
│  │  - Select agency/project type    │   │
│  │  - Watch review progress         │   │
│  │  - View findings + flagged sheets│   │
│  └──────────────────────────────────┘   │
│  ┌──────────────────────────────────┐   │
│  │  API Routes                      │   │
│  │  - /api/upload (PDF intake)      │   │
│  │  - /api/review/[id] (status)     │   │
│  │  - /api/findings/[id] (results)  │   │
│  └──────────────────────────────────┘   │
└────────────────┬────────────────────────┘
                 │
    ┌────────────▼────────────────┐
    │    Cloud Run (GCP)          │
    │  ┌──────────────────────┐   │
    │  │  Orchestrator        │   │
    │  │  - PDF extraction    │   │
    │  │  - Phase management  │   │
    │  │  - Subagent dispatch │   │
    │  │  - Progress tracking │   │
    │  │  60 min timeout      │   │
    │  └──────────┬───────────┘   │
    └─────────────┼───────────────┘
                  │
    ┌─────────────▼───────────────┐
    │   Vercel Sandbox            │
    │  ┌──────────────────────┐   │
    │  │  Agent SDK Execution │   │
    │  │  - Skill discovery   │   │
    │  │  - Subagent runs     │   │
    │  │  - File I/O          │   │
    │  │  - Vision calls      │   │
    │  └──────────────────────┘   │
    └─────────────────────────────┘
                  │
    ┌─────────────▼───────────────┐
    │   Supabase                  │
    │  - Projects table           │
    │  - Findings table           │
    │  - Storage (PDFs, PNGs)     │
    │  - Realtime subscriptions   │
    │    (no polling)             │
    └─────────────────────────────┘
```

## Cost Model

### Development Costs (API)

| Phase | Cost | Notes |
|-------|------|-------|
| Phase 0: Skills | $0 | Pro subscription only |
| Phase 1: Vision validation | $0 | Pro subscription, upload PNGs to claude.ai |
| Phase 2: Agent SDK pipeline | $50-75 | Test ladder approach, ~5 L3 iterations + 2-3 L4 runs |
| Phase 3: Deployment | $20-30 | Mostly debugging deploy issues, not pipeline logic |
| **Total estimated** | **$70-105** | Following CrossBeam's test ladder discipline |

### Production Run Costs (estimated)

| Operation | Model | Est. Cost | Duration |
|-----------|-------|-----------|----------|
| Sheet manifest | Sonnet | ~$0.50 | ~1 min |
| Triage scan (20 sheets) | Sonnet | ~$2-3 | ~3-5 min |
| Deep extraction (20 sheets) | Opus | ~$5-8 | ~10-15 min |
| Cross-reference analysis | Opus | ~$2-3 | ~3-5 min |
| Output generation | Sonnet | ~$0.50 | ~1 min |
| **Total per plan set** | | **~$10-15** | **~20-30 min** |

### CrossBeam Actual Benchmarks (for comparison)

| Flow | Cost | Duration |
|------|------|----------|
| City review (15 sheets, 7 subagents) | $8.69 | 16 min |
| Contractor analysis | $4.46 | 11 min |
| Contractor response | $1.46 | 6 min |
| Full contractor E2E | $5.92 | 17 min |

## Cost Control Strategies (from CrossBeam learnings)

1. **Model tiering:** L0/L1 tests use Haiku ($0.02-0.10). L2 uses Sonnet (~$1-2). Opus only at L3+.
2. **Pre-populated fixtures:** Run pipeline once, save intermediate JSONs, test downstream phases independently.
3. **Offline shortcuts:** Remove web tools from allowedTools when testing with pre-baked skills.
4. **Scope reduction at L3:** Test with 1-3 sheets instead of full set. Validate orchestration, not coverage.
5. **Budget caps:** Every `query()` call has `maxBudgetUsd`. L0=$0.10, L1=$1.00, L3=$8.00, L4=$15.00.
6. **Test wrapper at L3, not L4:** Catch flow wrapper bugs at $2-3, not $7-10.

## Test Ladder Design

| Level | What it tests | Model | Budget | Duration |
|-------|---------------|-------|--------|----------|
| L0 | SDK init, skill discovery | Haiku | $0.10 | ~15s |
| L1 | Single skill invocation, ref file loading | Haiku/Sonnet | $1.00 | ~1 min |
| L2 | PDF extraction + single sheet vision | Sonnet | $2.00 | ~3 min |
| L3a | Triage scan on 3 sheets | Sonnet | $3.00 | ~5 min |
| L3b | Deep extraction on 1 sheet, structured output | Opus | $3.00 | ~5 min |
| L3c | Cross-reference on pre-populated fixtures | Opus | $5.00 | ~5 min |
| L4 | Full pipeline, all sheets, end-to-end | Opus | $15.00 | ~25 min |

## Test Data

| Asset | Location | Use |
|-------|----------|-----|
| FNC Farms Ph. 1 Civils (75MB) | `References/240704 - FNC Farms Ph. 1_Civils_26.02.11.pdf` | Primary test set — utility/lift station project, already reviewed |
| Corridor Rev 3 | `References/240085_CORRIDOR_REV 3_PLAN SET.pdf` | Secondary test — roadway/paving project |
| RFIs folder | `RFIs/` | Ground truth — actual RFIs from construction, validates what the tool should catch |

The RFIs folder is gold for validation. If the tool can catch issues that later became actual RFIs on the Corridor project, that's a powerful demo.

## Development Phases

### Phase 0: Skills Development (Week 1-2, $0)
- [ ] Decompose one agency's improvement standards into skill reference files
- [ ] Build plan-conventions skill (sheet naming, abbreviations, detail callout conventions)
- [ ] Build extraction-schemas (JSON schemas for structured vision output)
- [ ] Test skills in claude.ai with manual questions

### Phase 1: Vision Validation (Week 2-3, $0) — ✅ COMPLETE
- [x] Extract sample pages from both plan sets as PNGs
- [x] Test structured extraction on: plan view, profile view, detail sheet, cover/index sheet
- [x] Determine: can vision reliably read pipe callouts, detail bubbles, inverts, material specs?
- [x] Refine extraction prompts based on results
- [x] Document what works and what doesn't
- **Results:** See `docs/findings/PHASE1-VISION-FINDINGS.md`

### Phase 2: Agent SDK Pipeline (Week 3-5, $50-75)
- [ ] Set up local Agent SDK project following CrossBeam scaffolding
- [ ] Build test ladder L0→L4
- [ ] Implement Phase 1 (intake) and Phase 2 (triage)
- [ ] Implement Phase 3 (deep extraction) — one page per subagent
- [ ] Implement Phase 4 (cross-reference analysis)
- [ ] Implement Phase 5 (output generation)
- [ ] Validate against FNC Farms (known findings)
- [ ] Cross-check against Corridor RFIs

### Phase 3: Deployment (Week 5-7, $20-30)
- [ ] Deploy orchestrator to Cloud Run
- [ ] Deploy frontend to Vercel
- [ ] Set up Supabase (projects, findings, storage, realtime)
- [ ] Wire up Agent SDK execution via Vercel Sandbox
- [ ] End-to-end test with uploaded plan set
- [ ] Demo-ready state

## Open Questions

1. ~~**Vision reliability on civil sheets:** Can Opus/Sonnet read dense civil engineering drawings accurately enough for structured extraction?~~ **ANSWERED (Phase 1):** Yes, with tiling. Quarter-page crops at 300 DPI work. Full pages fail.

2. ~~**Profile view extraction:** How well does vision parse overlapping profile data?~~ **ANSWERED (Phase 1):** Works well on half-page profile crops. SS/W label confusion is a known issue; hybrid extraction (text layer + vision) should fix it.

3. **Detail sheets:** Standard detail sheets have 6-12 details per sheet with varying scales. Can vision isolate individual details and match them to callout references?

4. ~~**Cross-reference matching logic:** What's the best data structure?~~ **ANSWERED:** Graph structure. Nodes = structures, edges = pipes. Station-keyed with fuzzy matching for format variations (STA 12+50 vs 12+50.00).

5. **Output format:** What format is most useful for a reviewing PE? Structured JSON for further processing? Markdown report? Annotated PDF? Probably all three, prioritized by usefulness. Triage UI (accept/reject per finding) is the long-term goal.

6. **Plan set size limits:** FNC Farms is 75MB. Typical plan sets can be 100-200MB for large projects. What are the practical limits for upload, extraction, and processing time?

7. **Hybrid extraction validation:** How much of the FNC Farms PDF has a usable text layer? Need to test `get_text("dict")` on representative pages to confirm we get text + bounding boxes. If the PDF is rasterized/scanned, hybrid won't work and we fall back to vision-only.

8. **Tile overlap deduplication:** When the same structure callout appears in two overlapping tiles, how do we deduplicate? Likely: match by station + offset within tolerance, keep the extraction from whichever tile had the callout more centered.

## Scope & Limitations

This tool verifies **explicitly labeled data only** — callouts, annotations, dimensions, and text that appears on the plans. It cannot:
- Scale un-dimensioned distances off a PDF (e.g., verifying 10' separation between water and sewer if drawn to scale but not dimensioned)
- Make engineering judgment calls (constructability, hydraulic adequacy, etc.)
- Verify information that exists only in specifications or geotechnical reports (not on the plans)
- Replace PE review — it flags issues for the PE to evaluate

## Future Roadmap (post-MVP)

- **Delta/Revision Review:** Store graph from Rev 1, diff against Rev 2 graph to verify redline corrections were picked up. Huge time-saver for re-submittals.
- **HITL Triage Dashboard:** "Tinder for RFIs" — show finding + cropped plan screenshot, PE clicks accept/reject. Filters false positives before generating final report.
- **Multi-discipline cross-check:** Compare civil plans against architectural site plans, landscape plans, or electrical plans for coordination conflicts.

## Existing Prototype

`plan_reviewer.py` — Streamlit app using OpenRouter (Gemini Flash) for basic plan review. Has sheet categorization via `SHEET_CATEGORIES` dict. Good starting point for understanding the problem but needs to be rebuilt on the Agent SDK architecture for production use.

## Related Past Work

- AutoCAD Civil 3D MCP interface exploration (Oct 2025)
- AionUi multi-agent GUI evaluation (Jan 2026)
- Civil PE Reviewer skill (installed at `/mnt/skills/user/civil-pe-reviewer/`)
- Prompts.chat MCP server setup for skill discovery
- CrossBeam deep dive and analysis (Feb 2026)
