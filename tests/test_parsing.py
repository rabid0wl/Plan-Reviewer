"""Unit tests for station/offset parsing helpers."""

from __future__ import annotations

import unittest

from src.utils.parsing import parse_offset, parse_signed_offset, parse_station


class ParsingTests(unittest.TestCase):
    def test_parse_station_variants(self) -> None:
        self.assertEqual(parse_station("16+82.45"), 1682.45)
        self.assertEqual(parse_station("STA: 13+40.73,  28.00'  RT"), 1340.73)
        self.assertEqual(parse_station("10+00"), 1000.0)
        self.assertEqual(parse_station("9+94.00"), 994.0)
        self.assertIsNone(parse_station("NO STATION HERE"))

    def test_parse_offset_variants(self) -> None:
        self.assertEqual(parse_offset("28.00' RT"), (28.0, "RT"))
        self.assertEqual(parse_offset("6.00' LT"), (6.0, "LT"))
        self.assertEqual(parse_offset("45.00' RTGB"), (45.0, "RT"))
        self.assertEqual(parse_offset("6.00 L"), (6.0, "LT"))
        self.assertEqual(parse_offset("28.00 R"), (28.0, "RT"))
        self.assertIsNone(parse_offset("OFFSET UNKNOWN"))

    def test_parse_signed_offset(self) -> None:
        self.assertEqual(parse_signed_offset("28.00' RT"), 28.0)
        self.assertEqual(parse_signed_offset("6.00' LT"), -6.0)
        self.assertEqual(parse_signed_offset("28.00 R"), 28.0)
        self.assertEqual(parse_signed_offset("6.00 L"), -6.0)
        self.assertIsNone(parse_signed_offset("OFFSET UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
