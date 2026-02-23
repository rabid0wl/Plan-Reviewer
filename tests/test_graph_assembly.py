"""Unit tests for utility graph assembly."""

from __future__ import annotations

import unittest

from src.extraction.schemas import TileExtraction
from src.graph.assembly import build_utility_graph


def _extraction(payload: dict) -> TileExtraction:
    return TileExtraction.model_validate(payload)


class GraphAssemblyTests(unittest.TestCase):
    def test_build_graph_keeps_orphan_pipe_and_quality_summary(self) -> None:
        extraction = _extraction(
            {
                "tile_id": "p14_r0_c1",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": [
                    {
                        "id": "SDMH-1",
                        "structure_type": "SDMH",
                        "station": "13+40.73",
                        "offset": "28.00' RT",
                        "source_text_ids": [10, 11],
                    }
                ],
                "pipes": [
                    {
                        "pipe_type": "SD",
                        "size": '12"',
                        "length_lf": 100.0,
                        "slope": 0.0040,
                        "to_station": "13+40.73",
                        "to_structure_hint": "SDMH-1",
                        "source_text_ids": [20],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        graph = build_utility_graph(
            extractions=[extraction],
            utility_type="SD",
            tile_meta_by_id={
                "p14_r0_c1": {"status": "ok", "sanitized": True},
                "p14_r0_c2": {"status": "skipped_low_coherence"},
            },
        )

        self.assertGreaterEqual(graph.number_of_nodes(), 2)
        self.assertEqual(graph.number_of_edges(), 1)
        edge_data = list(graph.edges(data=True))[0][2]
        self.assertEqual(edge_data.get("pipe_type"), "SD")
        self.assertEqual(edge_data.get("from_match_confidence"), "none")
        self.assertEqual(edge_data.get("to_match_confidence"), "high")

        summary = graph.graph.get("quality_summary", {})
        self.assertEqual(summary.get("total_tiles"), 2)
        self.assertEqual(summary.get("sanitized_tiles"), 1)
        self.assertEqual(summary.get("skipped_tiles"), 1)
        self.assertIn("Extraction quality below threshold", " | ".join(summary.get("warnings", [])))

    def test_pipe_dedup_keeps_highest_confidence(self) -> None:
        base_structures = [
            {
                "id": "A",
                "structure_type": "SDMH",
                "station": "10+00.00",
                "offset": "10.00' RT",
                "source_text_ids": [1],
            },
            {
                "id": "B",
                "structure_type": "SDMH",
                "station": "11+00.00",
                "offset": "10.00' RT",
                "source_text_ids": [2],
            },
        ]
        high_conf = _extraction(
            {
                "tile_id": "p14_r0_c1",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": base_structures,
                "pipes": [
                    {
                        "pipe_type": "SD",
                        "size": '12"',
                        "length_lf": 100.0,
                        "slope": 0.0030,
                        "from_station": "10+00.00",
                        "to_station": "11+00.00",
                        "from_structure_hint": "A",
                        "to_structure_hint": "B",
                        "notes": "primary",
                        "source_text_ids": [10],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        low_conf_reversed = _extraction(
            {
                "tile_id": "p14_r1_c1",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": base_structures,
                "pipes": [
                    {
                        "pipe_type": "SD",
                        "size": '12"',
                        "length_lf": 100.0,
                        "slope": 0.0030,
                        "from_station": "11+00.00",
                        "from_structure_hint": "B",
                        "notes": "secondary copy",
                        "source_text_ids": [20],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        graph = build_utility_graph(
            extractions=[high_conf, low_conf_reversed],
            utility_type="SD",
            tile_meta_by_id={},
        )
        self.assertEqual(graph.number_of_edges(), 1)
        edge = list(graph.edges(data=True))[0][2]
        self.assertEqual(edge.get("matched_confidence"), "high")
        self.assertEqual(edge.get("from_station"), "10+00.00")
        self.assertEqual(edge.get("to_station"), "11+00.00")
        self.assertEqual(sorted(edge.get("source_tile_ids", [])), ["p14_r0_c1", "p14_r1_c1"])
        self.assertEqual(sorted(edge.get("source_text_ids", [])), [10, 20])

    def test_pipe_dedup_reversed_direction(self) -> None:
        structures = [
            {
                "id": "A",
                "structure_type": "SDMH",
                "station": "20+00.00",
                "offset": "12.00' RT",
                "source_text_ids": [1],
            },
            {
                "id": "B",
                "structure_type": "SDMH",
                "station": "21+00.00",
                "offset": "12.00' RT",
                "source_text_ids": [2],
            },
        ]
        e1 = _extraction(
            {
                "tile_id": "p14_r0_c2",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": structures,
                "pipes": [
                    {
                        "pipe_type": "SD",
                        "size": '12"',
                        "length_lf": 100.0,
                        "slope": 0.0020,
                        "from_station": "20+00.00",
                        "to_station": "21+00.00",
                        "from_structure_hint": "A",
                        "to_structure_hint": "B",
                        "notes": "copy one",
                        "source_text_ids": [30],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        e2 = _extraction(
            {
                "tile_id": "p14_r1_c2",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": structures,
                "pipes": [
                    {
                        "pipe_type": "SD",
                        "size": '12"',
                        "length_lf": 100.0,
                        "slope": 0.0020,
                        "from_station": "21+00.00",
                        "to_station": "20+00.00",
                        "from_structure_hint": "B",
                        "to_structure_hint": "A",
                        "notes": "copy two with longer notes",
                        "source_text_ids": [31],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        graph = build_utility_graph(extractions=[e1, e2], utility_type="SD", tile_meta_by_id={})
        self.assertEqual(graph.number_of_edges(), 1)
        edge = list(graph.edges(data=True))[0][2]
        self.assertEqual(sorted(edge.get("source_tile_ids", [])), ["p14_r0_c2", "p14_r1_c2"])
        self.assertEqual(sorted(edge.get("source_text_ids", [])), [30, 31])

    def test_reference_only_page_fallback_flags_misclassified_tile(self) -> None:
        signing_tile = _extraction(
            {
                "tile_id": "p103_r0_c0",
                "page_number": 103,
                "sheet_type": "signing_striping",
                "utility_types_present": ["SS"],
                "structures": [],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        misclassified_plan_tile = _extraction(
            {
                "tile_id": "p103_r0_c1",
                "page_number": 103,
                "sheet_type": "plan_view",
                "utility_types_present": ["SS"],
                "structures": [
                    {
                        "id": "SSMH-A",
                        "structure_type": "SSMH",
                        "station": "10+00.00",
                        "offset": "6.00' RT",
                        "source_text_ids": [1],
                    }
                ],
                "pipes": [
                    {
                        "pipe_type": "SS",
                        "size": '8"',
                        "from_station": "10+00.00",
                        "source_text_ids": [2],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        graph = build_utility_graph(
            extractions=[signing_tile, misclassified_plan_tile],
            utility_type="SS",
            tile_meta_by_id={},
        )
        edge = list(graph.edges(data=True))[0][2]
        self.assertTrue(edge.get("is_reference_only"))

    def test_reference_only_fallback_not_applied_when_profile_view_present(self) -> None:
        signing_tile = _extraction(
            {
                "tile_id": "p103_r0_c0",
                "page_number": 103,
                "sheet_type": "signing_striping",
                "utility_types_present": ["SS"],
                "structures": [],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        profile_tile = _extraction(
            {
                "tile_id": "p103_r0_c1",
                "page_number": 103,
                "sheet_type": "profile_view",
                "utility_types_present": ["SS"],
                "structures": [],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        plan_tile = _extraction(
            {
                "tile_id": "p103_r0_c2",
                "page_number": 103,
                "sheet_type": "plan_view",
                "utility_types_present": ["SS"],
                "structures": [
                    {
                        "id": "SSMH-A",
                        "structure_type": "SSMH",
                        "station": "10+00.00",
                        "offset": "6.00' RT",
                        "source_text_ids": [1],
                    }
                ],
                "pipes": [
                    {
                        "pipe_type": "SS",
                        "size": '8"',
                        "from_station": "10+00.00",
                        "source_text_ids": [2],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        graph = build_utility_graph(
            extractions=[signing_tile, profile_tile, plan_tile],
            utility_type="SS",
            tile_meta_by_id={},
        )
        edge = list(graph.edges(data=True))[0][2]
        self.assertFalse(edge.get("is_reference_only"))

    def test_signing_striping_tile_is_always_reference_only(self) -> None:
        signing_tile = _extraction(
            {
                "tile_id": "p103_r0_c0",
                "page_number": 103,
                "sheet_type": "signing_striping",
                "utility_types_present": ["SS"],
                "structures": [
                    {
                        "id": "SSMH-A",
                        "structure_type": "SSMH",
                        "station": "10+00.00",
                        "offset": "6.00' RT",
                        "source_text_ids": [1],
                    }
                ],
                "pipes": [
                    {
                        "pipe_type": "SS",
                        "size": '8"',
                        "from_station": "10+00.00",
                        "source_text_ids": [2],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        profile_tile = _extraction(
            {
                "tile_id": "p103_r0_c1",
                "page_number": 103,
                "sheet_type": "profile_view",
                "utility_types_present": ["SS"],
                "structures": [],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        graph = build_utility_graph(
            extractions=[signing_tile, profile_tile],
            utility_type="SS",
            tile_meta_by_id={},
        )
        edge = list(graph.edges(data=True))[0][2]
        self.assertTrue(edge.get("is_reference_only"))

    def test_gravity_orientation_flips_uphill_edge(self) -> None:
        extraction = _extraction(
            {
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
                        "inverts": [
                            {
                                "direction": "E",
                                "pipe_size": '8"',
                                "pipe_type": "SS",
                                "elevation": 291.0,
                                "source_text_ids": [1],
                            }
                        ],
                        "source_text_ids": [10],
                    },
                    {
                        "id": "B",
                        "structure_type": "SSMH",
                        "station": "12+00.00",
                        "offset": "6.00' RT",
                        "inverts": [
                            {
                                "direction": "W",
                                "pipe_size": '8"',
                                "pipe_type": "SS",
                                "elevation": 294.0,
                                "source_text_ids": [2],
                            }
                        ],
                        "source_text_ids": [20],
                    },
                ],
                "pipes": [
                    {
                        "pipe_type": "SS",
                        "size": '8"',
                        "length_lf": 200.0,
                        "slope": 0.015,
                        "from_station": "10+00.00",
                        "to_station": "12+00.00",
                        "from_structure_hint": "A",
                        "to_structure_hint": "B",
                        "source_text_ids": [30],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        graph = build_utility_graph(extractions=[extraction], utility_type="SS", tile_meta_by_id={})
        edges = list(graph.edges(data=True))
        self.assertEqual(len(edges), 1)
        from_node, to_node, edge_data = edges[0]
        from_invert = graph.nodes[from_node].get("representative_invert")
        to_invert = graph.nodes[to_node].get("representative_invert")
        self.assertGreater(from_invert, to_invert)
        self.assertTrue(edge_data.get("oriented_by_gravity"))
        self.assertEqual(edge_data.get("original_from_node"), to_node)
        self.assertEqual(edge_data.get("original_to_node"), from_node)

    def test_gravity_orientation_preserves_correct_direction(self) -> None:
        extraction = _extraction(
            {
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
                        "inverts": [
                            {
                                "direction": "W",
                                "pipe_size": '8"',
                                "pipe_type": "SS",
                                "elevation": 296.0,
                                "source_text_ids": [1],
                            }
                        ],
                        "source_text_ids": [10],
                    },
                    {
                        "id": "DN",
                        "structure_type": "SSMH",
                        "station": "12+00.00",
                        "offset": "6.00' RT",
                        "inverts": [
                            {
                                "direction": "E",
                                "pipe_size": '8"',
                                "pipe_type": "SS",
                                "elevation": 294.0,
                                "source_text_ids": [2],
                            }
                        ],
                        "source_text_ids": [20],
                    },
                ],
                "pipes": [
                    {
                        "pipe_type": "SS",
                        "size": '8"',
                        "length_lf": 200.0,
                        "slope": 0.010,
                        "from_station": "14+00.00",
                        "to_station": "12+00.00",
                        "from_structure_hint": "UP",
                        "to_structure_hint": "DN",
                        "source_text_ids": [30],
                    }
                ],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        graph = build_utility_graph(extractions=[extraction], utility_type="SS", tile_meta_by_id={})
        edges = list(graph.edges(data=True))
        self.assertEqual(len(edges), 1)
        from_node, to_node, edge_data = edges[0]
        from_invert = graph.nodes[from_node].get("representative_invert")
        to_invert = graph.nodes[to_node].get("representative_invert")
        self.assertGreaterEqual(from_invert, to_invert)
        self.assertFalse(edge_data.get("oriented_by_gravity", False))


if __name__ == "__main__":
    unittest.main()
