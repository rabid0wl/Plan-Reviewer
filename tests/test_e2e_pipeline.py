from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from src.graph.assembly import build_utility_graph, graph_to_dict, load_extractions_with_meta
from src.graph.checks import run_all_checks
from src.report.html_report import write_html_report


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class PipelineE2ETests(unittest.TestCase):
    def test_minimal_sd_pipeline_end_to_end(self) -> None:
        fixtures_dir = Path(__file__).parent / "fixtures" / "e2e_minimal"
        self.assertTrue(fixtures_dir.exists(), "Expected fixtures/e2e_minimal directory to exist")

        extractions, tile_meta = load_extractions_with_meta(fixtures_dir)
        self.assertGreaterEqual(len(extractions), 1)

        graph = build_utility_graph(
            extractions=extractions,
            utility_type="SD",
            tile_meta_by_id=tile_meta,
        )
        self.assertEqual(graph.graph.get("utility_type"), "SD")

        findings = run_all_checks(graph)
        graph_payload = graph_to_dict(graph)

        by_severity: Counter[str] = Counter()
        by_type: Counter[str] = Counter()
        for finding in findings:
            by_severity[finding.severity] += 1
            by_type[finding.finding_type] += 1

        findings_payload = {
            "utility_type": "SD",
            "graph": {
                "nodes": graph.number_of_nodes(),
                "edges": graph.number_of_edges(),
                "quality_summary": graph_payload.get("quality_summary", {}),
            },
            "counts": {
                "total_findings": len(findings),
                "by_severity": dict(by_severity),
                "by_type": dict(by_type),
            },
            "findings": [finding.to_dict() for finding in findings],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            graphs_dir = root / "graphs"
            findings_dir = root / "findings"
            out_path = root / "report.html"

            prefix = "calibration-clean"
            _write_json(graphs_dir / f"{prefix}-sd.json", graph_payload)
            _write_json(findings_dir / f"{prefix}-sd-findings.json", findings_payload)

            write_html_report(
                graphs_dir=graphs_dir,
                findings_dir=findings_dir,
                prefix=prefix,
                out_path=out_path,
            )
            html_text = out_path.read_text(encoding="utf-8")

            self.assertIn("Plan Review Report", html_text)
            self.assertIn("Storm Drain (SD)", html_text)
            self.assertIn("Structure Schedule", html_text)
            self.assertIn("Pipe Schedule", html_text)


if __name__ == "__main__":
    unittest.main()

