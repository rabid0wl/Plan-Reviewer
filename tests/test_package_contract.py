from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from src.extraction.package_contract import PackageArtifact, build_analysis_package_from_summary


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class PackageContractTests(unittest.TestCase):
    def test_build_analysis_package_from_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tiles_dir = root / "tiles"
            text_layers_dir = root / "text_layers"
            out_dir = root / "out"
            tile_id = "p14_r0_c1"
            tile_path = tiles_dir / f"{tile_id}.png"
            text_layer_path = text_layers_dir / f"{tile_id}.json"
            extraction_path = out_dir / f"{tile_id}.json"
            meta_path = out_dir / f"{tile_id}.json.meta.json"
            raw_path = out_dir / f"{tile_id}.json.raw.txt"

            tile_path.parent.mkdir(parents=True, exist_ok=True)
            tile_path.write_bytes(b"png")
            _write_json(
                text_layer_path,
                {
                    "tile_id": tile_id,
                    "page_number": 14,
                    "coherence_score": 0.9,
                    "is_hybrid_viable": True,
                    "items": [],
                },
            )
            _write_json(
                extraction_path,
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
            _write_json(
                meta_path,
                {
                    "status": "ok",
                    "tile_id": tile_id,
                    "page_number": 14,
                    "sanitized": False,
                    "coherence_score": 0.9,
                    "corrected_fields": [],
                },
            )
            raw_path.write_text("{}", encoding="utf-8")

            summary = {
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:10:00Z",
                "tiles_dir": str(tiles_dir),
                "text_layers_dir": str(text_layers_dir),
                "out_dir": str(out_dir),
                "model": "google/gemini-3-flash-preview",
                "escalation_model": "google/gemini-3-flash-preview",
                "allow_low_coherence": False,
                "escalation_enabled": True,
                "escalation_coherence_threshold": 0.7,
                "max_concurrency": 1,
                "counts": {
                    "total_candidates": 1,
                    "paired_tiles": 1,
                    "missing_text_layers": 0,
                    "ok": 1,
                    "dry_run": 0,
                    "skipped_low_coherence": 0,
                    "validation_error": 0,
                    "runtime_error": 0,
                },
                "results": [
                    {
                        "tile_stem": tile_id,
                        "tile_path": str(tile_path),
                        "text_layer_path": str(text_layer_path),
                        "out_path": str(extraction_path),
                        "meta_path": str(meta_path),
                        "raw_out_path": str(raw_path),
                        "status": "ok",
                        "meta": {
                            "tile_id": tile_id,
                            "page_number": 14,
                            "sanitized": False,
                            "coherence_score": 0.9,
                            "corrected_fields": [],
                        },
                    }
                ],
            }

            package = build_analysis_package_from_summary(summary, run_id="run-1")
            self.assertEqual(package.run_id, "run-1")
            self.assertEqual(package.counts.total_candidates, 1)
            self.assertEqual(len(package.artifacts), 1)
            self.assertEqual(package.artifacts[0].tile_id, tile_id)
            self.assertEqual(package.artifacts[0].status.value, "ok")
            self.assertIsNotNone(package.artifacts[0].hashes.extraction_sha256)

    def test_package_artifact_rejects_invalid_tile_id(self) -> None:
        with self.assertRaises(ValidationError):
            PackageArtifact.model_validate(
                {
                    "tile_id": "bad-tile",
                    "page_number": 1,
                    "status": "ok",
                    "paths": {
                        "tile_path": "a",
                        "text_layer_path": "b",
                        "extraction_path": "c",
                        "meta_path": "d",
                        "raw_path": "e",
                    },
                    "hashes": {
                        "extraction_sha256": None,
                        "meta_sha256": None,
                        "text_layer_sha256": None,
                    },
                    "meta_summary": {
                        "sanitized": False,
                        "coherence_score": 1.0,
                        "corrected_fields": [],
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
