from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.extraction.run_hybrid_batch import run_batch


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class RunHybridBatchContractTests(unittest.TestCase):
    def test_run_batch_writes_analysis_package_and_summary_contract_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tiles_dir = root / "tiles"
            text_layers_dir = root / "text_layers"
            out_dir = root / "extractions"
            summary_path = out_dir / "batch_summary.json"

            tile_id = "p14_r0_c1"
            tile_path = tiles_dir / f"{tile_id}.png"
            text_layer_path = text_layers_dir / f"{tile_id}.json"

            tile_path.parent.mkdir(parents=True, exist_ok=True)
            tile_path.write_bytes(b"png")
            _write_json(
                text_layer_path,
                {
                    "tile_id": tile_id,
                    "page_number": 14,
                    "coherence_score": 0.95,
                    "is_hybrid_viable": True,
                    "items": [],
                },
            )

            def fake_run_hybrid_extraction(**kwargs) -> int:
                out_path = Path(kwargs["output_path"])
                raw_path = Path(kwargs["raw_output_path"])
                meta_path = Path(kwargs["meta_output_path"])
                _write_json(
                    out_path,
                    {
                        "tile_id": tile_id,
                        "page_number": 14,
                        "sheet_type": "plan_view",
                        "utility_types_present": ["SD"],
                        "structures": [],
                        "pipes": [],
                        "callouts": [],
                        "street_names": [],
                        "lot_numbers": [],
                    },
                )
                raw_path.write_text("{}", encoding="utf-8")
                _write_json(
                    meta_path,
                    {
                        "status": "ok",
                        "tile_id": tile_id,
                        "page_number": 14,
                        "sanitized": False,
                        "coherence_score": 0.95,
                        "corrected_fields": [],
                    },
                )
                return 0

            with patch(
                "src.extraction.run_hybrid_batch.run_hybrid_extraction",
                side_effect=fake_run_hybrid_extraction,
            ):
                exit_code = run_batch(
                    tiles_dir=tiles_dir,
                    text_layers_dir=text_layers_dir,
                    out_dir=out_dir,
                    tile_globs=["*.png"],
                    max_tiles=None,
                    model="google/gemini-3-flash-preview",
                    api_key="dummy",
                    referer="https://planreviewer.local",
                    title="test",
                    temperature=0.0,
                    max_tokens=512,
                    timeout_sec=30,
                    allow_low_coherence=False,
                    dry_run=False,
                    no_cache=True,
                    use_json_schema=True,
                    prompt_dir=None,
                    fail_fast=False,
                    summary_out=summary_path,
                    escalation_model="google/gemini-3-flash-preview",
                    escalation_coherence_threshold=0.7,
                    escalation_enabled=True,
                    max_concurrency=1,
                )

            self.assertEqual(exit_code, 0)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary.get("contract_version"), "preanalysis.v1")
            self.assertTrue(isinstance(summary.get("run_id"), str))
            self.assertTrue(summary.get("analysis_package_path"))

            package_path = out_dir / "analysis_package.json"
            self.assertTrue(package_path.exists())
            package = json.loads(package_path.read_text(encoding="utf-8"))
            self.assertEqual(package.get("contract_version"), "preanalysis.v1")
            self.assertEqual(len(package.get("artifacts", [])), 1)
            self.assertEqual(package["artifacts"][0]["tile_id"], tile_id)


if __name__ == "__main__":
    unittest.main()
