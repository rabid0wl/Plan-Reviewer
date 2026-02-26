import streamlit as st
import fitz  # PyMuPDF
import re
import json
import base64
import requests
import os
import logging
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Use the repo-root .env by default so keys are shared with the CLI pipeline.
load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
DEFAULT_MODEL = "google/gemini-3-flash-preview"

# ── File logger ───────────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / f"reviewer_{datetime.now().strftime('%Y%m%d')}.log"),
        logging.StreamHandler(),  # also prints to terminal
    ]
)
log = logging.getLogger("plan_reviewer")

SHEET_CATEGORIES = {
    "cover":    ["COVER", "TITLE", "INDEX"],
    "general":  ["GENERAL NOTES", "NOTES", "LEGEND", "SYMBOLS"],
    "demo":     ["DEMO", "DEMOLITION", "REMOVAL"],
    "grading":  ["GRADING", "DRAINAGE", "EARTHWORK", "TOPO", "SURVEY"],
    "layout":   ["LAYOUT", "HORIZONTAL CONTROL", "ALIGNMENT"],
    "utility":  ["UTILITY", "WATER", "SEWER", "STORM", "DRAIN", "PIPE", "SS&W", "SD"],
    "paving":   ["PAVING", "AC", "HMA", "PAVEMENT", "TRENCH"],
    "signing":  ["SIGNING", "STRIPING", "MARKING", "TRAFFIC", "SSM"],
    "landscape":["LANDSCAPE", "PLANTING", "IRRIGATION", "WPCP", "POLLUTION"],
    "detail":   ["DETAIL", "STANDARD", "SECTION", "CROSS SECTION", "CURB RAMP"],
    "electrical":["ELECTRICAL", "LIGHTING", "CONDUIT"],
    "structural":["STRUCTURAL", "FRAMING", "FOUNDATION"],
}

st.set_page_config(page_title="Plan Set Reviewer", page_icon="📐", layout="wide")

st.markdown("""
<style>
.metric-card { background:#f8f9fa; border-radius:8px; padding:16px 20px; border-left:4px solid #0066cc; }
.issue-critical { border-left:4px solid #dc3545; background:#fff5f5; padding:10px 14px; border-radius:6px; margin:6px 0; }
.issue-warning  { border-left:4px solid #fd7e14; background:#fff8f0; padding:10px 14px; border-radius:6px; margin:6px 0; }
.issue-info     { border-left:4px solid #0d6efd; background:#f0f4ff; padding:10px 14px; border-radius:6px; margin:6px 0; }
.tag { display:inline-block; background:#e9ecef; border-radius:4px; padding:2px 8px; font-size:0.8em; margin:2px; font-family:monospace; }
.cat-badge { display:inline-block; background:#d1ecf1; border-radius:4px; padding:1px 6px; font-size:0.75em; margin:2px; color:#0c5460; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def pdf_page_to_base64(doc, page_num: int, dpi: int = 150) -> str:
    """Render a PDF page to a base64-encoded PNG."""
    page = doc[page_num]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    return base64.b64encode(pix.tobytes("png")).decode()


def call_vision_llm(image_b64: str, prompt: str, model: str) -> str:
    """Send an image + prompt to OpenRouter, return text response."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://planreviewer.4creeks.com",
        "X-Title": "Plan Set Reviewer",
    }
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"}
                },
                {"type": "text", "text": prompt}
            ]
        }],
        "temperature": 0,
        "max_tokens": 8192,
    }
    resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                         headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _log(logs: list, msg: str, level: str = "info"):
    """Write to both the in-memory UI log list and the file logger."""
    logs.append(msg)
    clean = re.sub(r"[📂📄🤖✅⚠️❌🔍📋]", "", msg).strip()
    getattr(log, level)(clean)


def categorize_sheet(description: str) -> str:
    """Assign a category to a sheet based on its description."""
    desc_upper = description.upper()
    for cat, keywords in SHEET_CATEGORIES.items():
        if any(kw in desc_upper for kw in keywords):
            return cat
    return "other"


def parse_index_with_llm(doc, model: str, logs: list) -> list:
    """
    Use vision LLM to extract the sheet index from the first 1-2 pages.
    Returns list of dicts: {number, code, description, category}
    """
    sheets = []

    # Try page 1, then page 2 if index not found
    for pg in range(min(2, len(doc))):
        _log(logs, f"📄 Sending page {pg + 1} to {model} for index extraction…")
        img_b64 = pdf_page_to_base64(doc, pg, dpi=150)

        prompt = """You are reviewing a civil engineering plan set cover sheet.

Your task: extract the complete sheet index / drawing list from this page.

Return ONLY a JSON array. Each entry must have:
- "number": the sequential sheet number as a plain integer string — no periods, no suffixes (e.g. "1" not "1.", "01" not "01.")
- "code": the sheet ID/code if present (e.g. "G1", "CD3", "AA7"), or "" if none
- "description": the sheet title/description (string)

Rules:
- Expand any ranges you see (e.g. "CD1-CD6" becomes 6 separate entries)
- If the index uses only numbers with no letter codes, leave "code" as ""
- If you cannot find a sheet index on this page, return an empty array []
- Do not include any explanation, just the JSON array

Example output:
[
  {"number": "01", "code": "G1", "description": "COVER SHEET"},
  {"number": "02", "code": "G2", "description": "GENERAL NOTES"},
  {"number": "03", "code": "D1", "description": "DEMO PLAN - KAMM AVE"}
]"""

        raw = call_vision_llm(img_b64, prompt, model)
        _log(logs, f"🤖 Raw LLM response (page {pg + 1}):\n{raw[:500]}{'…' if len(raw) > 500 else ''}")

        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()

        parsed = None
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            _log(logs, f"⚠️ JSON parse error on page {pg + 1}: {e} — attempting partial recovery…", "warning")
            # Salvage complete objects from truncated JSON.
            # Find all complete {...} objects and re-parse each individually.
            partial_matches = re.findall(r'\{[^{}]+\}', cleaned)
            recovered = []
            for obj_str in partial_matches:
                try:
                    recovered.append(json.loads(obj_str))
                except json.JSONDecodeError:
                    pass
            if recovered:
                parsed = recovered
                _log(logs, f"♻️ Recovered {len(recovered)} entries from partial response")

        if parsed and isinstance(parsed, list) and len(parsed) > 0:
            for s in parsed:
                s["category"] = categorize_sheet(s.get("description", ""))
            sheets = parsed
            _log(logs, f"✅ Extracted {len(sheets)} sheets from page {pg + 1}")

            # Warn if we likely got a truncated result (suspiciously few sheets)
            if len(sheets) < 10:
                _log(logs, f"⚠️ Only {len(sheets)} sheets extracted — index may be truncated. Check results carefully.", "warning")
            break
        else:
            _log(logs, f"⚠️ Page {pg + 1} returned no usable data, trying next page…", "warning")

    return sheets


def extract_all_references(doc, logs: list) -> dict:
    """
    Text-scan every page for sheet cross-references.
    Returns {normalized_code: set(page_nums)}
    """
    # Match "SHEET(S) <code>" — captures only the immediate code token(s)
    ref_pattern = re.compile(
        r'SHEET[S]?\s+([A-Z]{0,3}\d{1,3}(?:\s*[-,&/]\s*[A-Z]{0,3}\d{1,3})*)',
        re.IGNORECASE
    )
    code_pattern = re.compile(r'([A-Z]{0,3}\d{1,3})')
    noise = {'STA', 'END', 'REV', 'MAX', 'MIN', 'HMA', 'PVC', 'ADA', 'RFI', 'CA', 'NO'}

    # External reference signals — lines with these likely point outside the plan set
    external_signals = re.compile(
        r'CITY\s+STD|CALTRANS|STANDARD\s+DETAIL|STD\.\s*PLAN|COUNTY\s+STD',
        re.IGNORECASE
    )

    total_pages = len(doc)
    refs = {}

    for page_num in range(len(doc)):
        text = doc[page_num].get_text()
        for match in ref_pattern.finditer(text):
            # Get surrounding line for context checks
            line_start = text.rfind('\n', 0, match.start()) + 1
            line_end   = text.find('\n', match.end())
            line       = text[line_start: line_end if line_end > 0 else None]

            # Skip if this is clearly an external standard reference
            if external_signals.search(line):
                continue

            codes = code_pattern.findall(match.group(1))
            for code in codes:
                code = code.strip()
                if not code or code in noise:
                    continue

                # Normalize: strip leading zeros for pure numbers so "02" == "2"
                normalized = code.lstrip("0") or "0" if code.isdigit() else code

                # Filter pure numbers outside page count — can't be a valid sheet
                if normalized.isdigit() and int(normalized) > total_pages:
                    continue

                # Filter numbers that appear in decimal context (e.g. "328.46")
                # Check character immediately before the match start in the text
                pre = text[match.start()-1:match.start()] if match.start() > 0 else ""
                if pre == "." and normalized.isdigit():
                    continue

                refs.setdefault(normalized, set()).add(page_num + 1)

    _log(logs, f"🔍 Found {len(refs)} unique sheet references across all pages")
    return refs


def get_reference_context(doc, code: str, page_num: int) -> str:
    text = doc[page_num - 1].get_text()
    for line in text.split('\n'):
        if re.search(rf'\b{re.escape(code)}\b', line, re.IGNORECASE):
            return line.strip()
    return ""


def classify_severity(pages: set) -> str:
    if len(pages) >= 10:
        return "High"
    elif len(pages) >= 3:
        return "Medium"
    return "Low"


def run_analysis(pdf_bytes: bytes, model: str, filename: str = "unknown.pdf") -> dict:
    logs = []
    log.info(f"=== START ANALYSIS: {filename} | model={model} ===")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(doc)
    _log(logs, f"📂 Opened PDF: {total_pages} pages")

    # ── Step 1: Vision LLM extracts sheet index ───────────────────────────────
    sheets = parse_index_with_llm(doc, model, logs)

    # Build lookup sets — always include both codes AND normalized numbers so
    # references like "SHEET 2" resolve even in code-based plan sets (G2 = sheet 2).
    all_codes   = {s["code"].strip() for s in sheets if s["code"]}
    all_numbers = {s["number"].strip().rstrip(".").lstrip("0") or "0"
                   for s in sheets if s["number"]}
    index_lookup = all_codes | all_numbers   # always union, never either-or

    _log(logs, f"📋 Index lookup — codes: {len(all_codes)}, numbers: {len(all_numbers)}, total: {len(index_lookup)}")
    _log(logs, f"📋 Sample: {sorted(index_lookup)[:20]}{'…' if len(index_lookup) > 20 else ''}")

    # ── Step 2: Text-scan all pages for cross-references ─────────────────────
    all_refs = extract_all_references(doc, logs)

    # ── Step 3: Categorize refs ───────────────────────────────────────────────
    broken = {}
    valid = {}
    for code, pages in all_refs.items():
        # Normalize both sides: strip leading zeros for number-only codes
        normalized = code.lstrip("0") or "0" if code.isdigit() else code
        if normalized in index_lookup:
            valid[code] = pages
        else:
            broken[code] = pages

    _log(logs, f"✅ Valid refs: {len(valid)}  ❌ Broken refs: {len(broken)}")
    log.info(f"=== END ANALYSIS: {filename} | sheets={len(sheets)} valid={len(valid)} broken={len(broken)} ===")

    # ── Step 4: Build issues list ─────────────────────────────────────────────
    issues = []
    for code, pages in sorted(broken.items(), key=lambda x: -len(x[1])):
        severity = classify_severity(pages)
        sample_pages = sorted(pages)[:3]
        contexts = []
        for pg in sample_pages:
            ctx = get_reference_context(doc, code, pg)
            if ctx:
                contexts.append(f"p.{pg}: {ctx}")
        issues.append({
            "code": code,
            "severity": severity,
            "page_count": len(pages),
            "pages": sorted(pages),
            "contexts": contexts,
        })

    doc.close()

    return {
        "total_pages": total_pages,
        "sheets": sheets,
        "total_sheets_in_index": len(sheets),
        "total_unique_refs": len(all_refs),
        "valid_refs": len(valid),
        "broken_refs": len(broken),
        "issues": issues,
        "logs": logs,
    }


# ── UI ────────────────────────────────────────────────────────────────────────

st.title("📐 Plan Set Reviewer")
st.caption("Cross-reference checker · Layer 1: Text Analysis  |  Layer 2: Vision Checks (coming soon)")

# Sidebar: settings
with st.sidebar:
    st.header("⚙️ Settings")
    model = st.selectbox(
        "Vision Model",
        options=[
            "google/gemini-3-flash-preview",
            "google/gemini-3-pro-preview",
            "google/gemini-2.5-pro",
            "google/gemini-2.5-flash",
            "anthropic/claude-sonnet-4.6",
            "openai/gpt-4.1",
        ],
        index=0,
        help="Model used for sheet index extraction. Flash = faster/cheaper, Pro = more accurate."
    )
    if not OPENROUTER_API_KEY:
        st.error("No OPENROUTER_API_KEY found in .env file at the repository root.")

uploaded = st.file_uploader(
    "Upload a PDF plan set",
    type=["pdf"],
    help="Supports multi-sheet civil engineering plan sets."
)

if uploaded:
    with st.spinner(f"Analyzing {uploaded.name}…"):
        results = run_analysis(uploaded.read(), model, filename=uploaded.name)

    # ── Summary metrics ───────────────────────────────────────────────────────
    st.divider()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Pages", results["total_pages"])
    c2.metric("Sheets in Index", results["total_sheets_in_index"])
    c3.metric("Valid References", results["valid_refs"])
    broken_count = results["broken_refs"]
    c4.metric(
        "Broken References",
        broken_count,
        delta=f"-{broken_count} issues" if broken_count else "Clean",
        delta_color="inverse"
    )

    st.divider()

    # ── Issues ────────────────────────────────────────────────────────────────
    if not results["issues"]:
        st.success("✅ No broken cross-references found.")
    else:
        high   = [i for i in results["issues"] if i["severity"] == "High"]
        medium = [i for i in results["issues"] if i["severity"] == "Medium"]
        low    = [i for i in results["issues"] if i["severity"] == "Low"]

        st.subheader("🔍 Broken Sheet References")
        st.caption("Sheet codes referenced in notes or keynotes that don't appear in the sheet index. "
                   "High = 10+ pages. Medium = 3–9 pages. Low = 1–2 pages.")

        def render_issue(issue):
            sev = issue["severity"]
            cls = {"High": "issue-critical", "Medium": "issue-warning", "Low": "issue-info"}[sev]
            badge = {"High": "🔴", "Medium": "🟠", "Low": "🔵"}[sev]
            pages_str = ", ".join(str(p) for p in issue["pages"][:10])
            if len(issue["pages"]) > 10:
                pages_str += f" … +{len(issue['pages'])-10} more"
            ctx_html = "".join(
                f'<div style="font-size:0.82em;color:#555;margin-top:4px;font-family:monospace">→ {c}</div>'
                for c in issue["contexts"]
            )
            st.markdown(f"""
            <div class="{cls}">
                <strong>{badge} {issue['code']}</strong>
                &nbsp;<span style="color:#888;font-size:0.85em">referenced on <strong>{issue['page_count']}</strong> page(s)</span>
                <div style="font-size:0.85em;margin-top:4px;color:#444">Pages: {pages_str}</div>
                {ctx_html}
            </div>""", unsafe_allow_html=True)

        if high:
            st.markdown("#### 🔴 High Priority")
            for i in high: render_issue(i)
        if medium:
            st.markdown("#### 🟠 Medium Priority")
            for i in medium: render_issue(i)
        if low:
            with st.expander(f"🔵 Low Priority ({len(low)} items)"):
                for i in low: render_issue(i)

    # ── Export ────────────────────────────────────────────────────────────────
    st.divider()
    export = {
        "file": uploaded.name,
        "model": model,
        "summary": {
            "total_pages": results["total_pages"],
            "sheets_in_index": results["total_sheets_in_index"],
            "broken_references": results["broken_refs"],
        },
        "sheet_index": results["sheets"],
        "issues": [
            {
                "sheet_code": i["code"],
                "severity": i["severity"],
                "referenced_on_pages": i["pages"],
                "sample_context": i["contexts"],
            }
            for i in results["issues"]
        ]
    }
    st.download_button(
        "⬇️ Export Issues as JSON",
        data=json.dumps(export, indent=2),
        file_name=f"{Path(uploaded.name).stem}_review.json",
        mime="application/json"
    )

    # ── Sheet index viewer ────────────────────────────────────────────────────
    with st.expander("📋 Parsed Sheet Index"):
        st.caption(f"{results['total_sheets_in_index']} sheets extracted by vision LLM")
        for sheet in results["sheets"]:
            code_str = f"`{sheet['code']}`" if sheet["code"] else f"#{sheet['number']}"
            cat = sheet.get("category", "other")
            st.markdown(
                f'{code_str} &nbsp;<span class="cat-badge">{cat}</span>&nbsp; {sheet["description"]}',
                unsafe_allow_html=True
            )

    # ── Layer 2 placeholder ───────────────────────────────────────────────────
    with st.expander("🧠 Layer 2: Vision Checks (coming soon)"):
        st.info(
            "**What's coming:**\n"
            "- Keynote bubble verification\n"
            "- Cross-sheet spec/dimension conflicts\n"
            "- Title block consistency\n"
            "- Standards compliance (city/county)\n"
            "- Per-discipline agent review"
        )

    # ── Logs ──────────────────────────────────────────────────────────────────
    with st.expander("🛠 Analysis Logs"):
        for line in results["logs"]:
            st.text(line)

else:
    st.info("Upload a PDF plan set above to begin analysis.")
    st.markdown("""
**What this checks (Layer 1 — Text Analysis):**
- Vision LLM reads the cover sheet and extracts the complete sheet index (any format)
- Scans all pages for cross-references (`SHEET C1`, `SHEETS S1-S8`, etc.)
- Flags any reference that doesn't resolve to a sheet in the index
- Categorizes sheets by discipline (grading, utility, signing, etc.)

**What's coming (Layer 2 — Vision Analysis):**
- Keynote bubbles, cross-sheet conflicts, title block consistency, standards compliance
""")
