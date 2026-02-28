# Plan Reviewer Architecture Flows

These diagrams are designed to be readable by non-coders while still matching the real runtime code path.

## 1) End-to-end architecture

```mermaid
flowchart TD
    A[Input PDF plan set]
    A --> B1
    A --> B2
    A --> B3

    subgraph Intake
      B1[tiler.py<br/>Render 3x2 overlapping tile images]
      B2[text_layer.py<br/>Extract exact text spans and coherence scores]
      B3[manifest.py<br/>Classify sheets and utility hints]
    end

    B1 --> C1[tiles/*.png]
    B2 --> C2[text_layers/*.json]
    B1 --> C3[tiles_index.json]
    B3 --> C4[manifest.json]

    C1 --> D
    C2 --> D

    subgraph Hybrid_Extraction
      D[run_hybrid_batch.py]
      D --> D1[Pair each tile and text-layer JSON]
      D1 --> D2[run_hybrid.py<br/>prompt plus model call]
      D2 --> D3[Schema validation and sanitizer recovery]
      D3 --> D4[Per tile outputs<br/>.json, .meta.json, .raw.txt]
      D4 --> D5[batch_summary.json]
      D4 --> D6[analysis_package.json<br/>hashes and contract metadata]
    end

    D6 --> E
    subgraph Pre_Analysis_Gate
      E[validate_package.py]
      E -->|fail| X[Stop and fix extraction package]
      E -->|pass or warn| F[Continue to graph assembly]
    end

    F --> G
    subgraph Graph_Assembly
      G[assembly.py]
      G --> G1[merge.py deduplicate structure nodes]
      G1 --> G2[Build SD, SS, W utility digraphs]
      G2 --> G3[Endpoint matching and orphan anchors]
      G3 --> G4[Crown filtering, edge dedup, gravity orientation]
      G4 --> G5[utility graph JSON output]
    end

    G5 --> H["graph.checks.run_all_checks()"]
    H --> I[prefix-utility-findings.json]
    G5 --> J[html_report.py]
    I --> J
    D5 --> J
    J --> K[Final HTML review report]
```

## 2) Extraction decision flow (zoomed in)

```mermaid
flowchart TD
    A[Start tile extraction<br/>tile.png plus text_layer.json] --> B[Load text-layer metadata]
    B --> C{Coherence below escalation threshold?}
    C -->|yes and escalation allowed| C1[Escalate once to fallback model]
    C1 --> Z1[Re-enter flow with escalated model]
    C -->|no| D{Hybrid viable OR allow_low_coherence?}
    C1 --> D
    D -->|no| D1[Write meta status skipped_low_coherence]
    D1 --> END0[Return success code]
    D -->|yes| E[Build hybrid prompt and cache key]
    E --> F{Cache hit with same key and model?}
    F -->|yes| F1{Cached result sanitized?}
    F1 -->|yes and escalation allowed| F2[Escalate reason sanitized_recovery_cached]
    F2 --> Z1
    F1 -->|no| END1[Use cached artifacts and return success]
    F -->|no| G{dry_run enabled?}
    G -->|yes| G1[Write dry_run meta and return success]
    G -->|no| H[Call OpenRouter]
    H --> H1[Try structured mode order:<br/>json_schema then json_object then none]
    H1 --> H2[Retry transient failures with backoff]
    H2 --> I{Model call failed?}
    I -->|yes and escalation allowed| I1[Escalate reason api_call_error]
    I1 --> Z1
    I -->|yes and no escalation| ERR0[Return runtime failure]
    I -->|no| J[Save raw model text]
    J --> K{Parse top-level JSON object?}
    K -->|no and escalation allowed| K1[Escalate reason json_parse_error]
    K1 --> Z1
    K -->|no and no escalation| ERR1[Write validation_error meta and return code 2]
    K -->|yes| L[Pre-correct tile_id and page_number from text layer]
    L --> M{Schema validation passes?}
    M -->|yes| N[Continue]
    M -->|no| M1[Sanitize payload by dropping invalid rows]
    M1 --> M2{Validation passes after sanitize?}
    M2 -->|no and escalation allowed| M3[Escalate reason schema_validation_error]
    M3 --> Z1
    M2 -->|no and no escalation| ERR2[Write validation_error meta and return code 2]
    M2 -->|yes| N1[Mark sanitized true]
    N1 --> N
    N --> O{Sanitized true and escalation allowed?}
    O -->|yes| O1[Escalate reason sanitized_recovery]
    O1 --> Z1
    O -->|no| P[Correct any metadata mismatch]
    P --> Q[Write validated extraction JSON]
    Q --> R[Write ok meta with usage counts]
    R --> END2[Return success code]
```

## Mental model with accurate analogies

- Intake is the scanning room:
  - `tiler.py` cuts each big sheet into readable pieces.
  - `text_layer.py` records every exact number and where it came from.
- Hybrid extraction is two reviewers in one seat:
  - vision reads layout and context,
  - text-layer data supplies exact numeric values.
- Package validation is shipping QA:
  - verifies files, hashes, and quality ratio before graph analysis.
- Graph assembly is converting notes into a network map:
  - structures become nodes and pipes become edges.
- Deterministic checks are engineering inspectors:
  - slope math, flow direction, connectivity, and elevation consistency.
- HTML report is the briefing packet:
  - findings table plus extracted schedules and quality warnings.

## How to view in editor

- Open this file in markdown preview to render both Mermaid diagrams.
- Raw diagram sources are also available as:
  - `docs/diagrams/app-architecture.mmd`
  - `docs/diagrams/extraction-decision-flow.mmd`
