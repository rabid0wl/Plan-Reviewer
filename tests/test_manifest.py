from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.intake import manifest


class ManifestClassificationTests(unittest.TestCase):
    def test_classify_sheet_type_uses_keyword_counts(self) -> None:
        cover_text = "CIVIL IMPROVEMENT PLANS\nSHEET INDEX\nVICINITY MAP"
        notes_text = "GENERAL NOTES\nABBREVIATIONS\nLEGEND"
        plan_text = "STORM DRAIN PLAN AND PROFILE\nUTILITY PLAN"

        self.assertEqual(manifest._classify_sheet_type(cover_text), "cover")
        self.assertEqual(manifest._classify_sheet_type(notes_text), "notes")
        self.assertEqual(manifest._classify_sheet_type(plan_text), "plan_view")

    def test_extract_utility_types_handles_sd_ss_w_signals(self) -> None:
        text = "STORM DRAIN PLAN\nSANITARY SEWER AND WATER PLAN"
        utility_types = manifest._extract_utility_types(text)
        self.assertIn("SD", utility_types)
        self.assertIn("SS", utility_types)
        self.assertIn("W", utility_types)

    def test_parse_cover_sheet_index_builds_label_to_description_map(self) -> None:
        index_text = "\n".join(
            [
                "C-1 COVER SHEET",
                "C2 GENERAL NOTES",
                "U1 - STORM DRAIN PLAN AND PROFILE",
                "SS2 SANITARY SEWER PLAN",
            ]
        )
        parsed = manifest._parse_cover_sheet_index(index_text)
        self.assertEqual(parsed.get("C-1"), "COVER SHEET")
        self.assertEqual(parsed.get("C-2"), "GENERAL NOTES")
        self.assertEqual(parsed.get("U-1"), "STORM DRAIN PLAN AND PROFILE")
        self.assertEqual(parsed.get("SS-2"), "SANITARY SEWER PLAN")


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self, mode: str, clip=None) -> str:  # noqa: D401 - test helper
        return self._text


class _FakeDoc:
    def __init__(self, pages: list[_FakePage]) -> None:
        self._pages = pages

    def __enter__(self) -> "_FakeDoc":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def __len__(self) -> int:
        return len(self._pages)

    def __getitem__(self, idx: int) -> _FakePage:
        return self._pages[idx]

    def __iter__(self):
        return iter(self._pages)


class ManifestBuildTests(unittest.TestCase):
    def test_build_manifest_uses_cover_index_and_title_block(self) -> None:
        cover_page_text = "\n".join(
            [
                "CIVIL IMPROVEMENT PLANS",
                "SHEET INDEX",
                "VICINITY MAP",
                "C-1 COVER SHEET",
            ]
        )
        utility_page_text = "U1 STORM DRAIN PLAN AND PROFILE\nSANITARY SEWER AND WATER PLAN"

        fake_doc = _FakeDoc(
            pages=[
                _FakePage(cover_page_text),
                _FakePage(utility_page_text),
            ]
        )

        def fake_extract_title_block_text(page) -> str:  # noqa: D401 - test helper
            return page.get_text("text")

        pdf_path = Path("dummy.pdf")
        with patch("src.intake.manifest.fitz.open", return_value=fake_doc), patch.object(
            manifest, "_extract_title_block_text", side_effect=fake_extract_title_block_text
        ):
            sheets = manifest.build_manifest(pdf_path)

        self.assertEqual(len(sheets), 2)

        cover = next(sheet for sheet in sheets if sheet.sheet_type == "cover")
        self.assertEqual(cover.page_number, 1)
        self.assertEqual(cover.sheet_label, "C-1")
        self.assertEqual(cover.description, "COVER SHEET")
        self.assertEqual(cover.utility_types, [])
        self.assertFalse(cover.needs_deep_extraction)

        utility = next(sheet for sheet in sheets if sheet.sheet_type == "plan_view")
        self.assertEqual(utility.page_number, 2)
        self.assertEqual(utility.sheet_label, "U-1")
        self.assertEqual(utility.description, "STORM DRAIN PLAN AND PROFILE")
        self.assertIn("SD", utility.utility_types)
        self.assertIn("SS", utility.utility_types)
        self.assertIn("W", utility.utility_types)
        self.assertTrue(utility.needs_deep_extraction)


if __name__ == "__main__":
    unittest.main()

