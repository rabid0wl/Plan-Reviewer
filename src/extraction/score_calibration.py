"""Score extraction outputs against known calibration checks."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from ..utils.parsing import parse_station

logger = logging.getLogger(__name__)


def _load_extractions(extractions_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(extractions_dir.glob("*.json")):
        name = path.name
        if name in {"batch_summary.json", "batch_summary_final.json", "calibration_score.json"}:
            continue
        if name.endswith(".meta.json"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        rows.append(payload)
    return rows


def _float_close(a: float | None, b: float, tol: float) -> bool:
    return a is not None and abs(a - b) <= tol


def _norm_size(size: str | None) -> str:
    if not size:
        return ""
    return size.replace(" ", "").upper()


def _station_close(station_str: str | None, target_station: str, tol_ft: float) -> bool:
    if not station_str:
        return False
    s_val = parse_station(station_str)
    t_val = parse_station(target_station)
    if s_val is None or t_val is None:
        return False
    return abs(s_val - t_val) <= tol_ft


def _contains_offset(offset: str | None, expected_fragment: str) -> bool:
    if not offset:
        return False
    return expected_fragment.upper() in offset.upper()


def _find_structure(
    extractions: list[dict[str, Any]],
    *,
    page_number: int,
    structure_type: str,
    target_station: str,
    station_tol_ft: float,
    offset_fragment: str | None = None,
) -> dict[str, Any] | None:
    stype = structure_type.upper()
    for ext in extractions:
        if int(ext.get("page_number", -1)) != page_number:
            continue
        for structure in ext.get("structures", []):
            found_type = str(structure.get("structure_type", "")).upper()
            if stype not in found_type:
                continue
            if not _station_close(structure.get("station"), target_station, station_tol_ft):
                continue
            if offset_fragment and not _contains_offset(structure.get("offset"), offset_fragment):
                continue
            return structure
    return None


def _find_pipe(
    extractions: list[dict[str, Any]],
    *,
    page_number: int,
    pipe_type: str,
    size: str,
    slope: float | None = None,
    slope_tol: float = 0.0003,
    length_lf: float | None = None,
    length_tol: float = 2.0,
) -> dict[str, Any] | None:
    ptype = pipe_type.upper()
    nsize = _norm_size(size)
    for ext in extractions:
        if int(ext.get("page_number", -1)) != page_number:
            continue
        for pipe in ext.get("pipes", []):
            found_type = str(pipe.get("pipe_type", "")).upper()
            found_size = _norm_size(pipe.get("size"))
            if ptype != found_type or nsize != found_size:
                continue

            if slope is not None:
                if not _float_close(pipe.get("slope"), slope, slope_tol):
                    continue
            if length_lf is not None:
                if not _float_close(pipe.get("length_lf"), length_lf, length_tol):
                    continue
            return pipe
    return None


def _check_p14(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    s_1682 = _find_structure(
        extractions,
        page_number=14,
        structure_type="SDMH",
        target_station="16+82.45",
        station_tol_ft=0.05,
        offset_fragment="RT",
    )
    checks.append(
        {
            "id": "p14_sdmh_1682",
            "description": "SDMH at STA 16+82.45, ~28.00' RT with rim ~305.95",
            "passed": s_1682 is not None
            and _float_close(s_1682.get("rim_elevation"), 305.95, 0.05)
            and _contains_offset(str(s_1682.get("offset")), "28.00"),
            "found_station": (s_1682 or {}).get("station"),
            "found_offset": (s_1682 or {}).get("offset"),
            "found_rim": (s_1682 or {}).get("rim_elevation"),
        }
    )

    e_inv = None
    w_inv = None
    if s_1682:
        for inv in s_1682.get("inverts", []):
            direction = str(inv.get("direction", "")).upper()
            if direction == "E":
                e_inv = inv
            elif direction == "W":
                w_inv = inv

    checks.append(
        {
            "id": "p14_sdmh_1682_inv_e",
            "description": 'SDMH STA 16+82.45 has INV 12" E ~299.77',
            "passed": e_inv is not None
            and _norm_size(e_inv.get("pipe_size")) == _norm_size('12"')
            and _float_close(e_inv.get("elevation"), 299.77, 0.05),
            "found": e_inv,
        }
    )
    checks.append(
        {
            "id": "p14_sdmh_1682_inv_w",
            "description": 'SDMH STA 16+82.45 has INV 12" W ~299.77',
            "passed": w_inv is not None
            and _norm_size(w_inv.get("pipe_size")) == _norm_size('12"')
            and _float_close(w_inv.get("elevation"), 299.77, 0.05),
            "found": w_inv,
        }
    )

    pipe_003 = _find_pipe(
        extractions,
        page_number=14,
        pipe_type="SD",
        size='12"',
        slope=0.0030,
        slope_tol=0.0003,
        length_lf=342.0,
        length_tol=2.0,
    )
    checks.append(
        {
            "id": "p14_pipe_342_003",
            "description": 'SD pipe 12" ~342 LF @ S=0.0030',
            "passed": pipe_003 is not None,
            "found": pipe_003,
        }
    )

    pipe_002 = _find_pipe(
        extractions,
        page_number=14,
        pipe_type="SD",
        size='12"',
        slope=0.0020,
        slope_tol=0.0003,
    )
    checks.append(
        {
            "id": "p14_pipe_002",
            "description": 'SD pipe 12" @ S=0.0020 (length may be omitted)',
            "passed": pipe_002 is not None,
            "found": pipe_002,
        }
    )

    passed = sum(1 for c in checks if c["passed"])
    return {
        "name": "page14_ground_truth",
        "passed": passed,
        "total": len(checks),
        "checks": checks,
    }


def _check_p36(extractions: list[dict[str, Any]]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    expected_structures = [
        ("10+06.00", 301.76),
        ("12+07.59", 302.19),
        ("14+09.19", 302.90),
    ]

    for station, rim in expected_structures:
        found = _find_structure(
            extractions,
            page_number=36,
            structure_type="SSMH",
            target_station=station,
            station_tol_ft=2.0,
            offset_fragment="6.00",
        )
        checks.append(
            {
                "id": f"p36_ssmh_{station.replace('+', '_')}",
                "description": f"SSMH near STA {station}, 6.00' RT, rim near {rim:.2f}",
                "passed": found is not None
                and _float_close(found.get("rim_elevation"), rim, 0.6),
                "found_station": (found or {}).get("station"),
                "found_offset": (found or {}).get("offset"),
                "found_rim": (found or {}).get("rim_elevation"),
            }
        )

    pipe_201 = _find_pipe(
        extractions,
        page_number=36,
        pipe_type="SS",
        size='8"',
        slope=0.0050,
        slope_tol=0.0005,
        length_lf=201.0,
        length_tol=4.0,
    )
    checks.append(
        {
            "id": "p36_ss_pipe_201_005",
            "description": 'SS pipe 8" ~201 LF @ S=0.005',
            "passed": pipe_201 is not None,
            "found": pipe_201,
        }
    )

    pipe_300 = _find_pipe(
        extractions,
        page_number=36,
        pipe_type="SS",
        size='8"',
        slope=0.0050,
        slope_tol=0.0005,
        length_lf=300.0,
        length_tol=6.0,
    )
    checks.append(
        {
            "id": "p36_ss_pipe_300_005",
            "description": 'SS pipe 8" ~300 LF @ S=0.005',
            "passed": pipe_300 is not None,
            "found": pipe_300,
        }
    )

    passed = sum(1 for c in checks if c["passed"])
    return {
        "name": "page36_ground_truth",
        "passed": passed,
        "total": len(checks),
        "checks": checks,
    }


def score_calibration(extractions_dir: Path) -> dict[str, Any]:
    extractions = _load_extractions(extractions_dir)
    pages: dict[int, int] = {}
    for ext in extractions:
        p = int(ext.get("page_number", -1))
        pages[p] = pages.get(p, 0) + 1

    p14_report = _check_p14(extractions)
    p36_report = _check_p36(extractions)
    total_passed = p14_report["passed"] + p36_report["passed"]
    total_checks = p14_report["total"] + p36_report["total"]

    return {
        "extractions_dir": str(extractions_dir),
        "extraction_file_count": len(extractions),
        "page_distribution": dict(sorted(pages.items())),
        "overall": {
            "passed": total_passed,
            "total": total_checks,
            "pass_rate": (total_passed / total_checks) if total_checks else 0.0,
        },
        "reports": [p14_report, p36_report],
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score extraction outputs against calibration checks.")
    parser.add_argument(
        "--extractions-dir",
        type=Path,
        required=True,
        help="Directory containing validated extraction JSON files.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output path for calibration score JSON. Defaults to <extractions-dir>/calibration_score.json",
    )
    return parser


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    parser = _build_arg_parser()
    args = parser.parse_args()

    report = score_calibration(args.extractions_dir)
    out_path = args.out or (args.extractions_dir / "calibration_score.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    logger.info(
        "Calibration score: %s/%s (%.1f%%) | output=%s",
        report["overall"]["passed"],
        report["overall"]["total"],
        report["overall"]["pass_rate"] * 100.0,
        out_path,
    )


if __name__ == "__main__":
    main()
