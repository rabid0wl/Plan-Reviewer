"""Unit tests for hybrid extraction model escalation behavior."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.extraction.run_hybrid as run_hybrid
from src.extraction.run_hybrid import (
    DEFAULT_ESCALATION_MODEL,
    run_hybrid_extraction,
)

TEST_PRIMARY_MODEL = "google/gemini-2.5-flash-lite"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class HybridEscalationTests(unittest.TestCase):
    def test_call_openrouter_retries_without_response_format_on_400(self) -> None:
        class _FakeResponse:
            def __init__(self, status_code: int, payload: dict):
                self.status_code = status_code
                self._payload = payload

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise run_hybrid.requests.exceptions.HTTPError(f"status={self.status_code}")

            def json(self) -> dict:
                return self._payload

        captured_json_payloads: list[dict] = []

        def fake_post(_endpoint, *, headers, json, timeout):
            del headers, timeout
            captured_json_payloads.append(dict(json))
            if len(captured_json_payloads) == 1:
                return _FakeResponse(400, {"error": "bad request"})
            return _FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"tile_id":"p1_r0_c0","page_number":1}'
                            }
                        }
                    ]
                },
            )

        with patch("src.extraction.run_hybrid.requests.post", side_effect=fake_post):
            raw_text, response_json = run_hybrid.call_openrouter_vision(
                api_key="dummy",
                model=DEFAULT_ESCALATION_MODEL,
                prompt="prompt",
                image_data_url="data:image/png;base64,abcd",
                referer="https://planreviewer.local",
                title="test",
                temperature=0.0,
                max_tokens=256,
                timeout_sec=5,
                use_structured_output=True,
            )

        self.assertEqual(raw_text, '{"tile_id":"p1_r0_c0","page_number":1}')
        self.assertIn("choices", response_json)
        self.assertEqual(len(captured_json_payloads), 2)
        self.assertIn("response_format", captured_json_payloads[0])
        self.assertEqual(captured_json_payloads[0]["response_format"]["type"], "json_schema")
        self.assertIn("provider", captured_json_payloads[0])
        self.assertIn("response_format", captured_json_payloads[1])
        self.assertEqual(captured_json_payloads[1]["response_format"]["type"], "json_object")

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

    def test_null_page_number_recovered_from_text_layer(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile_path = root / "p26_r1_c0.png"
            text_layer_path = root / "p26_r1_c0.json"
            out_path = root / "p26_r1_c0.out.json"
            raw_out_path = root / "p26_r1_c0.raw.txt"
            meta_out_path = root / "p26_r1_c0.meta.json"

            tile_path.write_bytes(b"not-a-real-image")
            _write_json(
                text_layer_path,
                {
                    "tile_id": "p26_r1_c0",
                    "page_number": 26,
                    "coherence_score": 0.95,
                    "is_hybrid_viable": True,
                    "items": [],
                },
            )

            payload = {
                "tile_id": "p26_r1_c0",
                "page_number": None,
                "sheet_type": "profile_view",
                "utility_types_present": ["W"],
                "structures": [
                    {
                        "id": "W-TEE-1",
                        "structure_type": "tee",
                        "station": "20+00.00",
                        "offset": None,
                        "source_text_ids": [10],
                    }
                ],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }

            with patch(
                "src.extraction.run_hybrid.call_openrouter_vision",
                return_value=(json.dumps(payload), {"usage": {"cost": 0.001}}),
            ):
                exit_code = run_hybrid_extraction(
                    tile_path=tile_path,
                    text_layer_path=text_layer_path,
                    output_path=out_path,
                    raw_output_path=raw_out_path,
                    meta_output_path=meta_out_path,
                    model=DEFAULT_ESCALATION_MODEL,
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
                    escalation_enabled=False,
                )

            self.assertEqual(exit_code, 0)
            extraction = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(extraction.get("tile_id"), "p26_r1_c0")
            self.assertEqual(extraction.get("page_number"), 26)
            meta = json.loads(meta_out_path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("status"), "ok")

    def test_null_metadata_recovered_from_tile_id_when_text_layer_page_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile_path = root / "p26_r1_c0.png"
            text_layer_path = root / "p26_r1_c0.json"
            out_path = root / "p26_r1_c0.out.json"
            raw_out_path = root / "p26_r1_c0.raw.txt"
            meta_out_path = root / "p26_r1_c0.meta.json"

            tile_path.write_bytes(b"not-a-real-image")
            _write_json(
                text_layer_path,
                {
                    "tile_id": "p26_r1_c0",
                    "coherence_score": 0.95,
                    "is_hybrid_viable": True,
                    "items": [],
                },
            )

            payload = {
                "tile_id": None,
                "page_number": None,
                "sheet_type": "profile_view",
                "utility_types_present": ["W"],
                "structures": [],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
            }

            with patch(
                "src.extraction.run_hybrid.call_openrouter_vision",
                return_value=(json.dumps(payload), {"usage": {"cost": 0.001}}),
            ):
                exit_code = run_hybrid_extraction(
                    tile_path=tile_path,
                    text_layer_path=text_layer_path,
                    output_path=out_path,
                    raw_output_path=raw_out_path,
                    meta_output_path=meta_out_path,
                    model=DEFAULT_ESCALATION_MODEL,
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
                    escalation_enabled=False,
                )

            self.assertEqual(exit_code, 0)
            extraction = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(extraction.get("tile_id"), "p26_r1_c0")
            self.assertEqual(extraction.get("page_number"), 26)

    def test_bare_json_parsed_directly_without_regex(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile_path = root / "p1_r0_c0.png"
            text_layer_path = root / "p1_r0_c0.json"
            out_path = root / "p1_r0_c0.out.json"
            raw_out_path = root / "p1_r0_c0.raw.txt"
            meta_out_path = root / "p1_r0_c0.meta.json"

            tile_path.write_bytes(b"not-a-real-image")
            _write_json(
                text_layer_path,
                {
                    "tile_id": "p1_r0_c0",
                    "page_number": 1,
                    "coherence_score": 0.99,
                    "is_hybrid_viable": True,
                    "items": [],
                },
            )

            bare_payload = {
                "tile_id": "p1_r0_c0",
                "page_number": 1,
                "sheet_type": "plan_view",
                "utility_types_present": ["SD"],
                "structures": [],
                "pipes": [],
                "callouts": [],
                "street_names": [],
                "lot_numbers": [],
                "extraction_notes": None,
            }

            with patch(
                "src.extraction.run_hybrid.call_openrouter_vision",
                return_value=(json.dumps(bare_payload), {"usage": {"cost": 0.001}}),
            ):
                with patch(
                    "src.extraction.run_hybrid._extract_json_candidate",
                    wraps=run_hybrid._extract_json_candidate,
                ) as extract_candidate:
                    exit_code = run_hybrid_extraction(
                        tile_path=tile_path,
                        text_layer_path=text_layer_path,
                        output_path=out_path,
                        raw_output_path=raw_out_path,
                        meta_output_path=meta_out_path,
                        model=DEFAULT_ESCALATION_MODEL,
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
                        escalation_enabled=False,
                    )

            self.assertEqual(exit_code, 0)
            self.assertEqual(extract_candidate.call_count, 0)
            meta = json.loads(meta_out_path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("status"), "ok")

    def test_fenced_json_falls_back_to_regex_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tile_path = root / "p1_r0_c0.png"
            text_layer_path = root / "p1_r0_c0.json"
            out_path = root / "p1_r0_c0.out.json"
            raw_out_path = root / "p1_r0_c0.raw.txt"
            meta_out_path = root / "p1_r0_c0.meta.json"

            tile_path.write_bytes(b"not-a-real-image")
            _write_json(
                text_layer_path,
                {
                    "tile_id": "p1_r0_c0",
                    "page_number": 1,
                    "coherence_score": 0.99,
                    "is_hybrid_viable": True,
                    "items": [],
                },
            )

            fenced_json = (
                "```json\n"
                '{"tile_id":"p1_r0_c0","page_number":1,"sheet_type":"plan_view",'
                '"utility_types_present":["SD"],"structures":[],"pipes":[],"callouts":[],'
                '"street_names":[],"lot_numbers":[],"extraction_notes":null}\n'
                "```"
            )

            with patch(
                "src.extraction.run_hybrid.call_openrouter_vision",
                return_value=(fenced_json, {"usage": {"cost": 0.001}}),
            ):
                with patch(
                    "src.extraction.run_hybrid._extract_json_candidate",
                    wraps=run_hybrid._extract_json_candidate,
                ) as extract_candidate:
                    exit_code = run_hybrid_extraction(
                        tile_path=tile_path,
                        text_layer_path=text_layer_path,
                        output_path=out_path,
                        raw_output_path=raw_out_path,
                        meta_output_path=meta_out_path,
                        model=DEFAULT_ESCALATION_MODEL,
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
                        escalation_enabled=False,
                    )

            self.assertEqual(exit_code, 0)
            self.assertGreaterEqual(extract_candidate.call_count, 1)
            meta = json.loads(meta_out_path.read_text(encoding="utf-8"))
            self.assertEqual(meta.get("status"), "ok")


if __name__ == "__main__":
    unittest.main()
