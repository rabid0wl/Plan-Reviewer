"""Smoke tests for HTML report generation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.report.html_report import write_html_report


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class HtmlReportTests(unittest.TestCase):
    def test_report_generation_with_all_utilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            graphs_dir = root / "graphs"
            findings_dir = root / "findings"
            batch_summary_path = root / "batch_summary.json"
            out_path = root / "report.html"

            quality_summary = {
                "total_tiles": 10,
                "ok_tiles": 6,
                "sanitized_tiles": 3,
                "skipped_tiles": 1,
                "quality_grade": "C",
                "warnings": ["3 tiles had sanitizer recovery", "1 tile skipped (low coherence)"],
            }

            for utility in ("sd", "ss", "w"):
                graph_payload = {
                    "utility_type": utility.upper(),
                    "quality_summary": quality_summary,
                    "nodes": [
                        {
                            "node_id": f"{utility}:node:1",
                            "kind": "structure",
                            "structure_type": "MH",
                            "station": "14+00.00",
                            "station_ft": 1400.0,
                            "offset": "10.00' RT",
                            "size": '48"',
                            "rim_elevation": 301.25,
                            "inverts": [{"direction": "E", "elevation": 299.1}],
                            "notes": "node note",
                            "source_page_numbers": [14],
                            "source_tile_ids": ["p14_r0_c0"],
                        },
                        {
                            "node_id": f"{utility}:node:2",
                            "kind": "structure",
                            "structure_type": "MH",
                            "station": "15+00.00",
                            "station_ft": 1500.0,
                            "offset": "10.00' RT",
                            "size": '48"',
                            "rim_elevation": 300.75,
                            "inverts": [{"direction": "W", "elevation": 298.9}],
                            "notes": "node note 2",
                            "source_page_numbers": [14],
                            "source_tile_ids": ["p14_r0_c1"],
                        },
                    ],
                    "edges": [
                        {
                            "edge_id": f"{utility}:e1",
                            "from_node": f"{utility}:node:1",
                            "to_node": f"{utility}:node:2",
                            "size": '12"',
                            "length_lf": 100.0,
                            "slope": 0.005,
                            "notes": "pipe note",
                            "material": "RCP",
                            "matched_confidence": "high",
                            "source_page_numbers": [14],
                            "source_tile_ids": ["p14_r0_c0", "p14_r0_c1"],
                            "oriented_by_gravity": utility == "ss",
                        }
                    ],
                }
                findings_payload = {
                    "utility_type": utility.upper(),
                    "graph": {
                        "nodes": 2,
                        "edges": 1,
                        "quality_summary": quality_summary,
                    },
                    "counts": {
                        "total_findings": 1,
                        "by_severity": {"warning": 1},
                        "by_type": {"slope_mismatch": 1},
                    },
                    "findings": [
                        {
                            "finding_type": "slope_mismatch",
                            "severity": "warning",
                            "description": f"{utility.upper()} slope mismatch",
                            "source_sheets": [14],
                        }
                    ],
                }
                _write_json(graphs_dir / f"calibration-clean-{utility}.json", graph_payload)
                _write_json(
                    findings_dir / f"calibration-clean-{utility}-findings.json",
                    findings_payload,
                )

            _write_json(
                batch_summary_path,
                {
                    "model": "google/gemini-3-flash-preview",
                    "completed_at": "2026-02-22T22:00:00Z",
                    "results": [
                        {
                            "meta": {
                                "tile_id": "p14_r0_c0",
                                "coherence_score": 0.95,
                                "structures_count": 2,
                                "pipes_count": 1,
                                "callouts_count": 3,
                                "sanitized": False,
                                "usage": {"cost": 0.01},
                            }
                        },
                        {
                            "meta": {
                                "tile_id": "p36_r0_c0",
                                "coherence_score": 0.91,
                                "structures_count": 3,
                                "pipes_count": 2,
                                "callouts_count": 4,
                                "sanitized": True,
                                "dropped_invalid_counts": {"structures": 1},
                                "usage": {"cost": 0.02},
                            }
                        },
                    ],
                },
            )

            write_html_report(
                graphs_dir=graphs_dir,
                findings_dir=findings_dir,
                prefix="calibration-clean",
                batch_summary_path=batch_summary_path,
                out_path=out_path,
            )
            html_text = out_path.read_text(encoding="utf-8")
            self.assertIn("Plan Review Report", html_text)
            self.assertIn("Pages analyzed:</strong> 14, 36", html_text)
            self.assertIn("Total extraction cost:</strong> $0.03", html_text)
            self.assertIn("Warning: extraction quality below threshold", html_text)
            self.assertIn("SS slope mismatch", html_text)
            self.assertIn("gravity-oriented", html_text)

    def test_report_generation_with_missing_utilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            graphs_dir = root / "graphs"
            findings_dir = root / "findings"
            out_path = root / "report.html"

            _write_json(
                graphs_dir / "run-sd.json",
                {
                    "utility_type": "SD",
                    "quality_summary": {
                        "total_tiles": 4,
                        "ok_tiles": 4,
                        "sanitized_tiles": 0,
                        "skipped_tiles": 0,
                        "quality_grade": "A",
                        "warnings": [],
                    },
                    "nodes": [
                        {
                            "node_id": "sd:n1",
                            "kind": "structure",
                            "structure_type": "SDMH",
                            "station": "13+40.73",
                            "station_ft": 1340.73,
                            "offset": "28.00' RT",
                            "source_page_numbers": [14],
                            "source_tile_ids": ["p14_r0_c1"],
                            "inverts": [],
                        }
                    ],
                    "edges": [],
                },
            )
            _write_json(
                findings_dir / "run-sd-findings.json",
                {
                    "utility_type": "SD",
                    "graph": {
                        "nodes": 1,
                        "edges": 0,
                        "quality_summary": {
                            "total_tiles": 4,
                            "ok_tiles": 4,
                            "sanitized_tiles": 0,
                            "skipped_tiles": 0,
                            "quality_grade": "A",
                            "warnings": [],
                        },
                    },
                    "counts": {"total_findings": 0, "by_severity": {}, "by_type": {}},
                    "findings": [],
                },
            )

            write_html_report(
                graphs_dir=graphs_dir,
                findings_dir=findings_dir,
                prefix="run",
                out_path=out_path,
            )
            html_text = out_path.read_text(encoding="utf-8")
            self.assertIn("Data Warnings", html_text)
            self.assertIn("Missing graph JSON for SS", html_text)
            self.assertIn("Pages analyzed:</strong> 14", html_text)
            self.assertIn("Storm Drain (SD)", html_text)


if __name__ == "__main__":
    unittest.main()
