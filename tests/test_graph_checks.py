"""Unit tests for graph consistency checks."""

from __future__ import annotations

import unittest

import networkx as nx

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


if __name__ == "__main__":
    unittest.main()
