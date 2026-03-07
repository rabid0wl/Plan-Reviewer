"""Unit tests for src.pipeline — phase detection, utility detection, graph
round-trip, run ID format, and run directory layout."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import networkx as nx

from src.pipeline import (
    _checks_complete,
    _detect_utilities_from_manifest,
    _extraction_complete,
    _graph_from_dict,
    _graphs_complete,
    _make_run_id,
    _manifest_complete,
    _report_complete,
    _run_dirs,
    _tiling_complete,
    _validation_complete,
)


class PhaseDetectionTests(unittest.TestCase):
    """Test each _*_complete sentinel-file checker."""

    def test_tiling_complete_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            sentinel = run_dir / "intake" / "tiles_index.json"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("{}", encoding="utf-8")
            self.assertTrue(_tiling_complete(run_dir))

    def test_tiling_complete_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_tiling_complete(Path(tmpdir)))

    def test_manifest_complete_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            sentinel = run_dir / "intake" / "manifest.json"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("[]", encoding="utf-8")
            self.assertTrue(_manifest_complete(run_dir))

    def test_manifest_complete_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_manifest_complete(Path(tmpdir)))

    def test_extraction_complete_with_analysis_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            pkg = run_dir / "extractions" / "analysis_package.json"
            pkg.parent.mkdir(parents=True, exist_ok=True)
            pkg.write_text("{}", encoding="utf-8")
            self.assertTrue(_extraction_complete(run_dir))

    def test_extraction_complete_with_batch_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            summary = run_dir / "extractions" / "batch_summary.json"
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text("{}", encoding="utf-8")
            self.assertTrue(_extraction_complete(run_dir))

    def test_extraction_complete_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_extraction_complete(Path(tmpdir)))

    def test_validation_complete_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            sentinel = run_dir / "extractions" / "analysis_validation.json"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("{}", encoding="utf-8")
            self.assertTrue(_validation_complete(run_dir))

    def test_validation_complete_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_validation_complete(Path(tmpdir)))

    def test_graphs_complete_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            graphs_dir = run_dir / "graphs"
            graphs_dir.mkdir(parents=True, exist_ok=True)
            for ut in ("sd", "ss"):
                (graphs_dir / f"proj-{ut}.json").write_text("{}", encoding="utf-8")
            self.assertTrue(_graphs_complete(run_dir, ["SD", "SS"], "proj"))

    def test_graphs_complete_false_missing_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            graphs_dir = run_dir / "graphs"
            graphs_dir.mkdir(parents=True, exist_ok=True)
            (graphs_dir / "proj-sd.json").write_text("{}", encoding="utf-8")
            # Missing proj-ss.json
            self.assertFalse(_graphs_complete(run_dir, ["SD", "SS"], "proj"))

    def test_graphs_complete_false_empty_utilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_graphs_complete(Path(tmpdir), [], "proj"))

    def test_checks_complete_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            graphs_dir = run_dir / "graphs"
            graphs_dir.mkdir(parents=True, exist_ok=True)
            for ut in ("sd", "w"):
                (graphs_dir / f"proj-{ut}-findings.json").write_text("{}", encoding="utf-8")
            self.assertTrue(_checks_complete(run_dir, ["SD", "W"], "proj"))

    def test_checks_complete_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_checks_complete(Path(tmpdir), ["SD"], "proj"))

    def test_report_complete_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir)
            report_dir = run_dir / "report"
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / "proj_report.html").write_text("<html></html>", encoding="utf-8")
            self.assertTrue(_report_complete(run_dir, "proj"))

    def test_report_complete_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(_report_complete(Path(tmpdir), "proj"))


class DetectUtilitiesFromManifestTests(unittest.TestCase):
    """Test _detect_utilities_from_manifest with various manifest contents."""

    def _write_manifest(self, tmpdir: str, entries: list[dict]) -> Path:
        manifest_path = Path(tmpdir) / "manifest.json"
        manifest_path.write_text(json.dumps(entries), encoding="utf-8")
        return manifest_path

    def test_all_three_utilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_manifest(tmpdir, [
                {"page_number": 1, "utility_types": ["SD", "SS"]},
                {"page_number": 2, "utility_types": ["W"]},
            ])
            result = _detect_utilities_from_manifest(path)
            self.assertEqual(result, ["SD", "SS", "W"])

    def test_single_utility(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_manifest(tmpdir, [
                {"page_number": 1, "utility_types": ["SD"]},
                {"page_number": 2, "utility_types": ["SD"]},
            ])
            result = _detect_utilities_from_manifest(path)
            self.assertEqual(result, ["SD"])

    def test_empty_utilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_manifest(tmpdir, [
                {"page_number": 1, "utility_types": []},
            ])
            result = _detect_utilities_from_manifest(path)
            self.assertEqual(result, [])

    def test_no_utility_types_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_manifest(tmpdir, [
                {"page_number": 1},
            ])
            result = _detect_utilities_from_manifest(path)
            self.assertEqual(result, [])

    def test_ignores_unknown_utility_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_manifest(tmpdir, [
                {"page_number": 1, "utility_types": ["SD", "ELEC", "GAS"]},
            ])
            result = _detect_utilities_from_manifest(path)
            self.assertEqual(result, ["SD"])

    def test_stable_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_manifest(tmpdir, [
                {"page_number": 1, "utility_types": ["W"]},
                {"page_number": 2, "utility_types": ["SD"]},
                {"page_number": 3, "utility_types": ["SS"]},
            ])
            result = _detect_utilities_from_manifest(path)
            self.assertEqual(result, ["SD", "SS", "W"])

    def test_case_insensitive_utility_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_manifest(tmpdir, [
                {"page_number": 1, "utility_types": ["sd", "Ss", "w"]},
            ])
            result = _detect_utilities_from_manifest(path)
            self.assertEqual(result, ["SD", "SS", "W"])

    def test_missing_manifest_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "nonexistent.json"
            result = _detect_utilities_from_manifest(path)
            self.assertEqual(result, [])


class GraphFromDictRoundTripTests(unittest.TestCase):
    """Test _graph_from_dict round-trip: create graph -> serialize -> reconstruct."""

    def test_round_trip(self) -> None:
        original: nx.DiGraph = nx.DiGraph(
            utility_type="SD",
            quality_summary={"total_tiles": 6, "sanitized_tiles": 1},
        )
        original.add_node("SDMH-1", kind="structure", station="10+00.00", rim=305.5)
        original.add_node("SDMH-2", kind="structure", station="12+50.00", rim=303.2)
        original.add_edge(
            "SDMH-1",
            "SDMH-2",
            edge_id="e1",
            pipe_type="SD",
            size='12"',
            slope=0.005,
        )

        # Serialize using the same format as graph_to_dict.
        payload = {
            "utility_type": original.graph.get("utility_type"),
            "quality_summary": original.graph.get("quality_summary", {}),
            "nodes": [
                {"node_id": nid, **attrs}
                for nid, attrs in original.nodes(data=True)
            ],
            "edges": [
                {"from_node": u, "to_node": v, **attrs}
                for u, v, attrs in original.edges(data=True)
            ],
        }

        reconstructed = _graph_from_dict(payload)

        self.assertEqual(reconstructed.graph.get("utility_type"), "SD")
        self.assertEqual(
            reconstructed.graph.get("quality_summary"),
            {"total_tiles": 6, "sanitized_tiles": 1},
        )
        self.assertEqual(set(reconstructed.nodes), {"SDMH-1", "SDMH-2"})
        self.assertEqual(reconstructed.nodes["SDMH-1"]["kind"], "structure")
        self.assertEqual(reconstructed.nodes["SDMH-1"]["station"], "10+00.00")
        self.assertAlmostEqual(reconstructed.nodes["SDMH-1"]["rim"], 305.5)

        edges = list(reconstructed.edges(data=True))
        self.assertEqual(len(edges), 1)
        u, v, edata = edges[0]
        self.assertEqual(u, "SDMH-1")
        self.assertEqual(v, "SDMH-2")
        self.assertEqual(edata["pipe_type"], "SD")
        self.assertEqual(edata["size"], '12"')
        self.assertAlmostEqual(edata["slope"], 0.005)

    def test_empty_payload(self) -> None:
        graph = _graph_from_dict({})
        self.assertEqual(graph.number_of_nodes(), 0)
        self.assertEqual(graph.number_of_edges(), 0)

    def test_nodes_without_node_id_are_skipped(self) -> None:
        payload = {
            "utility_type": "SS",
            "nodes": [
                {"kind": "orphan"},  # no node_id
                {"node_id": "A", "kind": "structure"},
            ],
            "edges": [],
        }
        graph = _graph_from_dict(payload)
        self.assertEqual(graph.number_of_nodes(), 1)
        self.assertIn("A", graph.nodes)

    def test_edges_without_endpoints_are_skipped(self) -> None:
        payload = {
            "utility_type": "W",
            "nodes": [
                {"node_id": "A", "kind": "structure"},
                {"node_id": "B", "kind": "structure"},
            ],
            "edges": [
                {"from_node": "A"},  # no to_node
                {"to_node": "B"},  # no from_node
                {"from_node": "A", "to_node": "B", "edge_id": "e1"},
            ],
        }
        graph = _graph_from_dict(payload)
        self.assertEqual(graph.number_of_edges(), 1)

    def test_payload_not_mutated(self) -> None:
        payload = {
            "utility_type": "SD",
            "nodes": [{"node_id": "A", "kind": "structure"}],
            "edges": [{"from_node": "A", "to_node": "B", "edge_id": "e1"}],
        }
        # Deep copy for comparison.
        import copy
        original_payload = copy.deepcopy(payload)
        _graph_from_dict(payload)
        self.assertEqual(payload, original_payload)


class MakeRunIdTests(unittest.TestCase):
    """Test _make_run_id returns the expected format."""

    def test_format_matches_pattern(self) -> None:
        run_id = _make_run_id()
        pattern = r"^run_\d{8}_\d{6}$"
        self.assertRegex(run_id, pattern)

    def test_starts_with_run_prefix(self) -> None:
        run_id = _make_run_id()
        self.assertTrue(run_id.startswith("run_"))

    def test_returns_string(self) -> None:
        run_id = _make_run_id()
        self.assertIsInstance(run_id, str)


class RunDirsTests(unittest.TestCase):
    """Test _run_dirs returns the expected directory structure."""

    def test_expected_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_20260306_120000"
            dirs = _run_dirs(run_dir)
            expected_keys = {"intake", "tiles", "text_layers", "extractions", "graphs", "report"}
            self.assertEqual(set(dirs.keys()), expected_keys)

    def test_paths_are_children_of_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "run_20260306_120000"
            dirs = _run_dirs(run_dir)
            for key, path in dirs.items():
                self.assertTrue(
                    str(path).startswith(str(run_dir)),
                    f"Expected {key} path to be under run_dir, got {path}",
                )

    def test_tiles_under_intake(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / "myrun"
            dirs = _run_dirs(run_dir)
            self.assertEqual(dirs["tiles"], dirs["intake"] / "tiles")
            self.assertEqual(dirs["text_layers"], dirs["intake"] / "text_layers")

    def test_all_values_are_paths(self) -> None:
        dirs = _run_dirs(Path("/fake/run_dir"))
        for key, val in dirs.items():
            self.assertIsInstance(val, Path, f"{key} should be a Path")


if __name__ == "__main__":
    unittest.main()
