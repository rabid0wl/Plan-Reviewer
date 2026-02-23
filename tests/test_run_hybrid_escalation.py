"""Unit tests for hybrid extraction model escalation behavior."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.extraction.run_hybrid import (
    DEFAULT_ESCALATION_MODEL,
    run_hybrid_extraction,
)

TEST_PRIMARY_MODEL = "google/gemini-2.5-flash-lite"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class HybridEscalationTests(unittest.TestCase):
    def test_low_coherence_escalates_to_fallback_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile_path = root / "p14_r0_c0.png"
            text_layer_path = root / "p14_r0_c0.json"
            out_path = root / "p14_r0_c0.out.json"
            raw_out_path = root / "p14_r0_c0.raw.txt"
            meta_out_path = root / "p14_r0_c0.meta.json"

            tile_path.write_bytes(b"not-a-real-image")
            _write_json(
                text_layer_path,
                {
                    "tile_id": "p14_r0_c0",
                    "page_number": 14,
                    "coherence_score": 0.35,
                    "is_hybrid_viable": False,
                    "items": [],
                },
            )

            exit_code = run_hybrid_extraction(
                tile_path=tile_path,
                text_layer_path=text_layer_path,
                output_path=out_path,
                raw_output_path=raw_out_path,
                meta_output_path=meta_out_path,
                model=TEST_PRIMARY_MODEL,
                api_key="dummy",
                referer="https://planreviewer.local",
                title="test",
                temperature=0.0,
                max_tokens=1024,
                timeout_sec=30,
                allow_low_coherence=False,
                dry_run=True,
                no_cache=True,
                prompt_output_path=None,
                escalation_model=DEFAULT_ESCALATION_MODEL,
                escalation_enabled=True,
            )

            self.assertEqual(exit_code, 0)
            meta = json.loads(meta_out_path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("status"), "dry_run")
            self.assertEqual(meta.get("model"), DEFAULT_ESCALATION_MODEL)
            self.assertEqual(
                meta.get("attempted_models"),
                [TEST_PRIMARY_MODEL, DEFAULT_ESCALATION_MODEL],
            )
            self.assertTrue(meta.get("escalated"))
            self.assertEqual(meta.get("escalation_reason"), "low_coherence")

    def test_sanitized_primary_output_escalates_to_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile_path = root / "p14_r0_c1.png"
            text_layer_path = root / "p14_r0_c1.json"
            out_path = root / "p14_r0_c1.out.json"
            raw_out_path = root / "p14_r0_c1.raw.txt"
            meta_out_path = root / "p14_r0_c1.meta.json"

            tile_path.write_bytes(b"not-a-real-image")
            _write_json(
                text_layer_path,
                {
                    "tile_id": "p14_r0_c1",
                    "page_number": 14,
                    "coherence_score": 0.92,
                    "is_hybrid_viable": True,
                    "items": [],
                },
            )

            primary_payload = {
                "tile_id": "p14_r0_c1",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": [
                    {
                        "structure_type": "SDMH",
                        "station": "13+40.73",
                        "source_text_ids": [1],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }
            fallback_payload = {
                "tile_id": "p14_r0_c1",
                "page_number": 14,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": [
                    {
                        "structure_type": "SDMH",
                        "station": "13+40.73",
                        "offset": "28.00' RT",
                        "source_text_ids": [10],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }

            def fake_call_openrouter_vision(**kwargs):
                model = kwargs.get("model")
                if model == TEST_PRIMARY_MODEL:
                    return json.dumps(primary_payload), {"usage": {"cost": 0.001}}
                if model == DEFAULT_ESCALATION_MODEL:
                    return json.dumps(fallback_payload), {"usage": {"cost": 0.002}}
                raise AssertionError(f"Unexpected model: {model}")

            with patch("src.extraction.run_hybrid.call_openrouter_vision", side_effect=fake_call_openrouter_vision):
                exit_code = run_hybrid_extraction(
                    tile_path=tile_path,
                    text_layer_path=text_layer_path,
                    output_path=out_path,
                    raw_output_path=raw_out_path,
                    meta_output_path=meta_out_path,
                    model=TEST_PRIMARY_MODEL,
                    api_key="dummy",
                    referer="https://planreviewer.local",
                    title="test",
                    temperature=0.0,
                    max_tokens=1024,
                    timeout_sec=30,
                    allow_low_coherence=False,
                    dry_run=False,
                    no_cache=True,
                    prompt_output_path=None,
                    escalation_model=DEFAULT_ESCALATION_MODEL,
                    escalation_enabled=True,
                )

            self.assertEqual(exit_code, 0)
            extraction = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(len(extraction.get("structures", [])), 1)
            self.assertEqual(extraction["structures"][0]["offset"], "28.00' RT")

            meta = json.loads(meta_out_path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("status"), "ok")
            self.assertEqual(meta.get("model"), DEFAULT_ESCALATION_MODEL)
            self.assertEqual(
                meta.get("attempted_models"),
                [TEST_PRIMARY_MODEL, DEFAULT_ESCALATION_MODEL],
            )
            self.assertTrue(meta.get("escalated"))
            self.assertEqual(meta.get("escalation_reason"), "sanitized_recovery")
            self.assertFalse(meta.get("sanitized"))


if __name__ == "__main__":
    unittest.main()
