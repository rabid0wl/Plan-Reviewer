"""Unit tests for graph merge logic."""

from __future__ import annotations

import unittest

from src.extraction.schemas import TileExtraction
from src.graph.merge import merge_structures


def _extraction(payload: dict) -> TileExtraction:
    return TileExtraction.model_validate(payload)


class GraphMergeTests(unittest.TestCase):
    def test_merge_exact_duplicates(self) -> None:
        a = _extraction(
            {
                "tile_id": "p14_r0_c1",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": [
                    {
                        "id": "SDMH-1",
                        "structure_type": "SDMH",
                        "station": "16+82.45",
                        "offset": "28.00' RT",
                        "source_text_ids": [10, 11],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        b = _extraction(
            {
                "tile_id": "p14_r1_c1",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": [
                    {
                        "id": "SDMH-1",
                        "structure_type": "SDMH",
                        "station": "16+82.45",
                        "offset": "28.00 RT",
                        "source_text_ids": [21, 22],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        merged = merge_structures(extractions=[a, b], utility_type="SD", tile_meta_by_id={})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].variants_count, 2)
        self.assertEqual(sorted(merged[0].source_tile_ids), ["p14_r0_c1", "p14_r1_c1"])

    def test_merge_degraded_copy_prefers_complete_record(self) -> None:
        complete = _extraction(
            {
                "tile_id": "p14_r0_c1",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": [
                    {
                        "id": "SDMH-1",
                        "structure_type": "SDMH",
                        "station": "16+82.45",
                        "offset": "28.00' RT",
                        "rim_elevation": 305.95,
                        "inverts": [
                            {
                                "direction": "E",
                                "pipe_size": '12"',
                                "pipe_type": "SD",
                                "elevation": 299.77,
                                "source_text_ids": [100],
                            },
                            {
                                "direction": "W",
                                "pipe_size": '12"',
                                "pipe_type": "SD",
                                "elevation": 299.77,
                                "source_text_ids": [101],
                            },
                        ],
                        "notes": 'INSTALL TYPE I (48") SDMH',
                        "source_text_ids": [10, 11, 12],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        degraded = _extraction(
            {
                "tile_id": "p14_r1_c2",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": [
                    {
                        "id": "SDMH-1",
                        "structure_type": "SDMH",
                        "station": "16+82.45",
                        "offset": "28.00' RT",
                        "notes": "ALL TYPE I (48\")",
                        "source_text_ids": [51],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        merged = merge_structures(extractions=[complete, degraded], utility_type="SD", tile_meta_by_id={})
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged[0].inverts), 2)
        self.assertIn("INSTALL TYPE I", merged[0].notes or "")

    def test_merge_three_way_overlap(self) -> None:
        payload = {
            "page_number": 14,
            "sheet_type": "plan_view",
            "utility_types_present": ["SD"],
            "structures": [
                {
                    "structure_type": "SDMH",
                    "station": "13+40.73",
                    "offset": "28.00' RT",
                    "source_text_ids": [1],
                }
            ],
            "pipes": [],
            "callouts": [],
            "street_names": [],
            "lot_numbers": [],
        }
        a = _extraction({"tile_id": "p14_r0_c1", **payload})
        b = _extraction({"tile_id": "p14_r1_c1", **payload})
        c = _extraction({"tile_id": "p14_r0_c2", **payload})

        merged = merge_structures(extractions=[a, b, c], utility_type="SD", tile_meta_by_id={})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].variants_count, 3)
        self.assertEqual(len(merged[0].source_tile_ids), 3)

    def test_gb_without_inverts_excluded_from_sd(self) -> None:
        extraction = _extraction(
            {
                "tile_id": "p14_r0_c0",
                "page_number": 14,
                "sheet_type": "grading",
                "utility_types_present": ["SD"],
                "structures": [
                    {
                        "structure_type": "GB",
                        "station": "10+57.64",
                        "offset": "45.00' RT",
                        "source_text_ids": [1],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        merged = merge_structures(extractions=[extraction], utility_type="SD", tile_meta_by_id={})
        self.assertEqual(len(merged), 0)

    def test_gb_with_inverts_included_in_sd(self) -> None:
        extraction = _extraction(
            {
                "tile_id": "p14_r0_c0",
                "page_number": 14,
                "sheet_type": "grading",
                "utility_types_present": ["SD"],
                "structures": [
                    {
                        "structure_type": "GB",
                        "station": "10+57.64",
                        "offset": "45.00' RT",
                        "inverts": [
                            {
                                "direction": "E",
                                "pipe_size": '12"',
                                "pipe_type": "SD",
                                "elevation": 301.0,
                                "source_text_ids": [2],
                            }
                        ],
                        "source_text_ids": [1],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        merged = merge_structures(extractions=[extraction], utility_type="SD", tile_meta_by_id={})
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].structure_type, "GB")

    def test_proximity_merge_collapses_nearby_structures(self) -> None:
        plan_view = _extraction(
            {
                "tile_id": "p36_r1_c0",
                "page_number": 36,
                "sheet_type": "plan_view",
                "utility_types_present": ["SS"],
                "structures": [
                    {
                        "structure_type": "SSMH",
                        "station": "10+06.00",
                        "offset": "6.00' RT",
                        "rim_elevation": 301.76,
                        "inverts": [
                            {
                                "direction": "E",
                                "pipe_size": '8"',
                                "pipe_type": "SS",
                                "elevation": 294.46,
                                "source_text_ids": [61],
                            }
                        ],
                        "source_text_ids": [60],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        profile_view = _extraction(
            {
                "tile_id": "p36_r1_c1",
                "page_number": 36,
                "sheet_type": "profile_view",
                "utility_types_present": ["SS"],
                "structures": [
                    {
                        "structure_type": "SSMH",
                        "station": "10+05.68",
                        "offset": "6.00' RT",
                        "rim_elevation": 300.89,
                        "inverts": [
                            {
                                "direction": "E",
                                "pipe_size": '8"',
                                "pipe_type": "SS",
                                "elevation": 291.18,
                                "source_text_ids": [70],
                            },
                            {
                                "direction": "W",
                                "pipe_size": '8"',
                                "pipe_type": "SS",
                                "elevation": 291.18,
                                "source_text_ids": [71],
                            },
                        ],
                        "source_text_ids": [69],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        merged = merge_structures(
            extractions=[plan_view, profile_view],
            utility_type="SS",
            tile_meta_by_id={},
        )
        self.assertEqual(len(merged), 1)
        self.assertIn("p36_r1_c0", merged[0].source_tile_ids)
        self.assertIn("p36_r1_c1", merged[0].source_tile_ids)
        self.assertEqual(len(merged[0].inverts), 2)

    def test_proximity_merge_preserves_distinct_structures(self) -> None:
        a = _extraction(
            {
                "tile_id": "p36_r0_c0",
                "page_number": 36,
                "sheet_type": "plan_view",
                "utility_types_present": ["SS"],
                "structures": [
                    {
                        "structure_type": "SSMH",
                        "station": "10+06.00",
                        "offset": "6.00' RT",
                        "source_text_ids": [1],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )
        b = _extraction(
            {
                "tile_id": "p36_r0_c1",
                "page_number": 36,
                "sheet_type": "plan_view",
                "utility_types_present": ["SS"],
                "structures": [
                    {
                        "structure_type": "SSMH",
                        "station": "12+07.59",
                        "offset": "6.00' RT",
                        "source_text_ids": [2],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
        )

        merged = merge_structures(extractions=[a, b], utility_type="SS", tile_meta_by_id={})
        self.assertEqual(len(merged), 2)


if __name__ == "__main__":
    unittest.main()
