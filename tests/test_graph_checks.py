"""Unit tests for graph consistency checks."""

from __future__ import annotations

import unittest

import networkx as nx

from src.graph.assembly import _filter_suspect_crowns
from src.graph.checks import check_connectivity, check_flow_direction, check_slope_consistency


class GraphChecksTests(unittest.TestCase):
    def test_slope_check(self) -> None:
        graph = nx.DiGraph(utility_type="SD")
        graph.add_node(
            "n_up",
            kind="structure",
            representative_invert=100.0,
            source_page_numbers=[14],
            source_text_ids=[1],
        )
        graph.add_node(
            "n_dn",
            kind="structure",
            representative_invert=99.0,
            source_page_numbers=[14],
            source_text_ids=[2],
        )
        graph.add_edge(
            "n_up",
            "n_dn",
            edge_id="e1",
            length_lf=100.0,
            slope=0.0050,
            source_page_numbers=[14],
            source_text_ids=[3],
        )

        findings = check_slope_consistency(graph)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_type, "slope_mismatch")

    def test_connectivity(self) -> None:
        graph = nx.DiGraph(utility_type="SD")
        graph.add_node("isolated", kind="structure", source_page_numbers=[14], source_text_ids=[10])
        graph.add_node("n2", kind="structure", source_page_numbers=[14], source_text_ids=[11])
        graph.add_node("orphan_anchor", kind="orphan_anchor", source_page_numbers=[14], source_text_ids=[])
        graph.add_edge(
            "n2",
            "orphan_anchor",
            edge_id="e_orphan",
            matched_confidence="none",
            source_page_numbers=[14],
            source_text_ids=[12],
        )

        findings = check_connectivity(graph)
        finding_types = {finding.finding_type for finding in findings}
        self.assertIn("orphan_node", finding_types)
        self.assertIn("unanchored_pipe", finding_types)

    def test_connectivity_unverifiable_when_no_structure_nodes(self) -> None:
        graph = nx.DiGraph(utility_type="W")
        graph.add_node("a1", kind="orphan_anchor")
        graph.add_node("a2", kind="orphan_anchor")
        graph.add_edge("a1", "a2", edge_id="w1", matched_confidence="none")

        findings = check_connectivity(graph)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_type, "connectivity_unverifiable")

    def test_reference_only_edge_suppresses_unanchored(self) -> None:
        graph = nx.DiGraph(utility_type="SS")
        graph.add_node("a1", kind="orphan_anchor")
        graph.add_node("a2", kind="orphan_anchor")
        graph.add_edge(
            "a1",
            "a2",
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

    def test_non_reference_edge_still_flags_unanchored(self) -> None:
        graph = nx.DiGraph(utility_type="SS")
        graph.add_node("s1", kind="structure", source_page_numbers=[24], source_text_ids=[1])
        graph.add_node("a1", kind="orphan_anchor")
        graph.add_edge(
            "s1",
            "a1",
            edge_id="real_edge",
            matched_confidence="none",
            is_reference_only=False,
            source_page_numbers=[24],
            source_text_ids=[2],
        )

        findings = check_connectivity(graph)
        finding_types = {f.finding_type for f in findings}
        self.assertIn("unanchored_pipe", finding_types)

    def test_flow_direction_backfall(self) -> None:
        graph = nx.DiGraph(utility_type="SS")
        graph.add_node("n_up", kind="structure", representative_invert=100.0, source_text_ids=[1])
        graph.add_node("n_dn", kind="structure", representative_invert=100.2, source_text_ids=[2])
        graph.add_edge("n_up", "n_dn", edge_id="e_backfall", source_page_numbers=[36], source_text_ids=[3])

        findings = check_flow_direction(graph)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_type, "flow_direction_error")

    def test_slope_uses_directional_invert(self) -> None:
        graph = nx.DiGraph(utility_type="SD")
        graph.add_node(
            "a",
            kind="structure",
            station_ft=1000.0,
            representative_invert=99.0,
            inverts=[
                {"direction": "E", "pipe_size": '12"', "elevation": 100.0},
                {"direction": "W", "pipe_size": '12"', "elevation": 99.0},
            ],
            source_page_numbers=[14],
            source_text_ids=[1],
        )
        graph.add_node(
            "b",
            kind="structure",
            station_ft=1100.0,
            representative_invert=98.0,
            inverts=[
                {"direction": "W", "pipe_size": '12"', "elevation": 99.5},
                {"direction": "E", "pipe_size": '12"', "elevation": 98.0},
            ],
            source_page_numbers=[14],
            source_text_ids=[2],
        )
        graph.add_edge(
            "a",
            "b",
            edge_id="e_dir",
            size='12"',
            length_lf=100.0,
            slope=0.0050,
            source_page_numbers=[14],
            source_text_ids=[3],
        )

        findings = check_slope_consistency(graph)
        self.assertEqual(len(findings), 0)

    def test_directional_invert_offset_fallback(self) -> None:
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
                {"direction": "N", "pipe_size": '12"', "elevation": 300.8},
                {"direction": "S", "pipe_size": '12"', "elevation": 300.9},
            ],
            source_page_numbers=[14],
            source_text_ids=[2],
        )
        graph.add_edge(
            "inlet",
            "sdmh",
            edge_id="e_same_sta",
            size='12"',
            length_lf=17.0,
            slope=0.0059,
            source_page_numbers=[14],
            source_text_ids=[3],
        )

        findings = check_slope_consistency(graph)
        slope_findings = [finding for finding in findings if finding.finding_type == "slope_mismatch"]
        self.assertEqual(len(slope_findings), 0)

    def test_filter_multi_invert_crown_to_crown_suspects(self) -> None:
        graph = nx.DiGraph(utility_type="SD")
        graph.add_node(
            "n1",
            kind="structure",
            inverts=[
                {"direction": "W", "pipe_size": '18"', "elevation": 320.48},
                {"direction": "N", "pipe_size": '18"', "elevation": 325.14},
            ],
            representative_invert=320.48,
        )

        _filter_suspect_crowns(graph)
        node = graph.nodes["n1"]
        self.assertEqual(len(node.get("crown_suspects", [])), 1)
        self.assertAlmostEqual(float(node["crown_suspects"][0]["elevation"]), 325.14, places=2)
        self.assertEqual(len(node.get("inverts", [])), 1)
        self.assertAlmostEqual(float(node["inverts"][0]["elevation"]), 320.48, places=2)
        self.assertAlmostEqual(float(node.get("representative_invert")), 320.48, places=2)

    def test_single_invert_high_drop_sets_crown_candidate(self) -> None:
        graph = nx.DiGraph(utility_type="SD")
        graph.add_node("up", kind="structure", representative_invert=325.30, inverts=[])
        graph.add_node("dn", kind="structure", representative_invert=320.25, inverts=[])
        graph.add_edge("up", "dn", edge_id="e_crown", slope=0.0011, length_lf=51.0)

        _filter_suspect_crowns(graph)
        edge = graph["up"]["dn"]
        self.assertTrue(edge.get("crown_contamination_candidate"))
        self.assertTrue(graph.nodes["up"].get("suspect_crown"))

    def test_slope_reclassified_to_crown_contamination_when_flagged(self) -> None:
        graph = nx.DiGraph(utility_type="SD")
        graph.add_node(
            "up",
            kind="structure",
            representative_invert=325.30,
            inverts=[],
            source_page_numbers=[24],
            source_text_ids=[1],
        )
        graph.add_node(
            "dn",
            kind="structure",
            representative_invert=320.55,
            inverts=[],
            source_page_numbers=[24],
            source_text_ids=[2],
        )
        graph.add_edge(
            "up",
            "dn",
            edge_id="e_crown_flagged",
            slope=0.0011,
            length_lf=51.0,
            crown_contamination_candidate=True,
            source_page_numbers=[24],
            source_text_ids=[3],
        )

        findings = check_slope_consistency(graph)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_type, "crown_contamination")
        self.assertEqual(findings[0].severity, "info")

    def test_non_crown_slope_mismatch_stays_warning(self) -> None:
        graph = nx.DiGraph(utility_type="SD")
        graph.add_node(
            "up",
            kind="structure",
            representative_invert=100.0,
            inverts=[],
            source_page_numbers=[14],
            source_text_ids=[1],
        )
        graph.add_node(
            "dn",
            kind="structure",
            representative_invert=99.0,
            inverts=[],
            source_page_numbers=[14],
            source_text_ids=[2],
        )
        graph.add_edge(
            "up",
            "dn",
            edge_id="e_non_crown",
            slope=0.0050,
            length_lf=100.0,
            source_page_numbers=[14],
            source_text_ids=[3],
        )

        findings = check_slope_consistency(graph)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_type, "slope_mismatch")
        self.assertEqual(findings[0].severity, "warning")

    def test_crown_filter_skips_water_utility(self) -> None:
        graph = nx.DiGraph(utility_type="W")
        original_inverts = [
            {"direction": "E", "pipe_size": '8"', "elevation": 100.0},
            {"direction": "W", "pipe_size": '8"', "elevation": 104.0},
        ]
        graph.add_node(
            "w1",
            kind="structure",
            inverts=[dict(row) for row in original_inverts],
            representative_invert=100.0,
        )

        _filter_suspect_crowns(graph)
        node = graph.nodes["w1"]
        self.assertNotIn("crown_suspects", node)
        self.assertEqual(node.get("inverts"), original_inverts)
        self.assertAlmostEqual(float(node.get("representative_invert")), 100.0, places=2)


if __name__ == "__main__":
    unittest.main()
