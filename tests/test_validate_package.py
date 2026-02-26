from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.extraction.package_contract import build_analysis_package_from_summary
from src.extraction.validate_package import validate_extraction_package
from src.utils.io_json import write_json_atomic


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _ok_extraction(tile_id: str, page_number: int) -> dict:
    return {
        "tile_id": tile_id,
        "page_number": page_number,
        "sheet_type": "plan_view",
        "utility_types_present": ["SD"],
        "structures": [],
        "pipes": [],
        "callouts": [],
        "street_names": [],
        "lot_numbers": [],
    }


class ValidatePackageTests(unittest.TestCase):
    def test_hash_mismatch_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tiles_dir = root / "tiles"
            text_layers_dir = root / "text_layers"
            out_dir = root / "extractions"
            tile_id = "p1_r0_c0"
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
                    "page_number": 1,
                    "coherence_score": 0.9,
                    "is_hybrid_viable": True,
                    "items": [],
                },
            )
            _write_json(extraction_path, _ok_extraction(tile_id, 1))
            _write_json(
                meta_path,
                {
                    "status": "ok",
                    "tile_id": tile_id,
                    "page_number": 1,
                    "sanitized": False,
                    "coherence_score": 0.9,
                    "corrected_fields": [],
                },
            )
            raw_path.write_text("{}", encoding="utf-8")

            summary = {
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
                            "page_number": 1,
                            "sanitized": False,
                            "coherence_score": 0.9,
                            "corrected_fields": [],
                        },
                    }
                ],
            }
            package = build_analysis_package_from_summary(summary, run_id="run-hash")
            package_path = out_dir / "analysis_package.json"
            write_json_atomic(package_path, package.model_dump(mode="json"), indent=2, sort_keys=True)

            # Corrupt the extraction after manifest hash generation.
            _write_json(extraction_path, _ok_extraction(tile_id, 99))

            report = validate_extraction_package(extractions_dir=out_dir)
            self.assertEqual(report.result.value, "fail")
            self.assertTrue(any("hash mismatch" in msg for msg in report.critical_errors))

    def test_quality_thresholds_warn_and_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tiles_dir = root / "tiles"
            text_layers_dir = root / "text_layers"
            out_dir = root / "extractions"

            # Two paired tiles; one sanitized + one skipped gives bad_ratio=1.0.
            for tile_id, page in (("p1_r0_c0", 1), ("p1_r0_c1", 1)):
                (tiles_dir / f"{tile_id}.png").parent.mkdir(parents=True, exist_ok=True)
                (tiles_dir / f"{tile_id}.png").write_bytes(b"png")
                _write_json(
                    text_layers_dir / f"{tile_id}.json",
                    {
                        "tile_id": tile_id,
                        "page_number": page,
                        "coherence_score": 0.9,
                        "is_hybrid_viable": True,
                        "items": [],
                    },
                )

            _write_json(out_dir / "p1_r0_c0.json", _ok_extraction("p1_r0_c0", 1))
            _write_json(
                out_dir / "p1_r0_c0.json.meta.json",
                {
                    "status": "ok",
                    "tile_id": "p1_r0_c0",
                    "page_number": 1,
                    "sanitized": True,
                    "coherence_score": 0.9,
                    "corrected_fields": [],
                },
            )
            _write_json(
                out_dir / "p1_r0_c1.json.meta.json",
                {
                    "status": "skipped_low_coherence",
                    "tile_id": "p1_r0_c1",
                    "page_number": 1,
                    "sanitized": False,
                    "coherence_score": 0.2,
                    "corrected_fields": [],
                },
            )
            (out_dir / "p1_r0_c0.json.raw.txt").write_text("{}", encoding="utf-8")

            summary = {
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
                    "total_candidates": 2,
                    "paired_tiles": 2,
                    "missing_text_layers": 0,
                    "ok": 1,
                    "dry_run": 0,
                    "skipped_low_coherence": 1,
                    "validation_error": 0,
                    "runtime_error": 0,
                },
                "results": [
                    {
                        "tile_stem": "p1_r0_c0",
                        "tile_path": str(tiles_dir / "p1_r0_c0.png"),
                        "text_layer_path": str(text_layers_dir / "p1_r0_c0.json"),
                        "out_path": str(out_dir / "p1_r0_c0.json"),
                        "meta_path": str(out_dir / "p1_r0_c0.json.meta.json"),
                        "raw_out_path": str(out_dir / "p1_r0_c0.json.raw.txt"),
                        "status": "ok",
                        "meta": {
                            "tile_id": "p1_r0_c0",
                            "page_number": 1,
                            "sanitized": True,
                            "coherence_score": 0.9,
                            "corrected_fields": [],
                        },
                    },
                    {
                        "tile_stem": "p1_r0_c1",
                        "tile_path": str(tiles_dir / "p1_r0_c1.png"),
                        "text_layer_path": str(text_layers_dir / "p1_r0_c1.json"),
                        "out_path": str(out_dir / "p1_r0_c1.json"),
                        "meta_path": str(out_dir / "p1_r0_c1.json.meta.json"),
                        "raw_out_path": str(out_dir / "p1_r0_c1.json.raw.txt"),
                        "status": "skipped_low_coherence",
                        "meta": {
                            "tile_id": "p1_r0_c1",
                            "page_number": 1,
                            "sanitized": False,
                            "coherence_score": 0.2,
                            "corrected_fields": [],
                        },
                    },
                ],
            }
            package = build_analysis_package_from_summary(summary, run_id="run-quality")
            package_path = out_dir / "analysis_package.json"
            write_json_atomic(package_path, package.model_dump(mode="json"), indent=2, sort_keys=True)

            warn_report = validate_extraction_package(
                extractions_dir=out_dir,
                quality_warn_threshold=0.15,
                quality_fail_threshold=2.0,
            )
            self.assertEqual(warn_report.result.value, "warn")

            fail_report = validate_extraction_package(
                extractions_dir=out_dir,
                quality_warn_threshold=0.15,
                quality_fail_threshold=0.30,
            )
            self.assertEqual(fail_report.result.value, "fail")
            self.assertTrue(any("quality gate exceeded fail threshold" in msg for msg in fail_report.critical_errors))

    def test_legacy_mode_migrates_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            tiles_dir = root / "tiles"
            text_layers_dir = root / "text_layers"
            out_dir = root / "extractions"
            tile_id = "p2_r0_c0"
            (tiles_dir / f"{tile_id}.png").parent.mkdir(parents=True, exist_ok=True)
            (tiles_dir / f"{tile_id}.png").write_bytes(b"png")
            _write_json(
                text_layers_dir / f"{tile_id}.json",
                {
                    "tile_id": tile_id,
                    "page_number": 2,
                    "coherence_score": 0.9,
                    "is_hybrid_viable": True,
                    "items": [],
                },
            )
            _write_json(out_dir / f"{tile_id}.json", _ok_extraction(tile_id, 2))
            _write_json(
                out_dir / f"{tile_id}.json.meta.json",
                {
                    "status": "ok",
                    "tile_id": tile_id,
                    "page_number": 2,
                    "sanitized": False,
                    "coherence_score": 0.9,
                    "corrected_fields": [],
                },
            )
            (out_dir / f"{tile_id}.json.raw.txt").write_text("{}", encoding="utf-8")

            summary = {
                "run_id": "legacy-run-1",
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
                        "tile_path": str(tiles_dir / f"{tile_id}.png"),
                        "text_layer_path": str(text_layers_dir / f"{tile_id}.json"),
                        "out_path": str(out_dir / f"{tile_id}.json"),
                        "meta_path": str(out_dir / f"{tile_id}.json.meta.json"),
                        "raw_out_path": str(out_dir / f"{tile_id}.json.raw.txt"),
                        "status": "ok",
                        "meta": {
                            "tile_id": tile_id,
                            "page_number": 2,
                            "sanitized": False,
                            "coherence_score": 0.9,
                            "corrected_fields": [],
                        },
                    }
                ],
            }
            _write_json(out_dir / "batch_summary.json", summary)

            report = validate_extraction_package(extractions_dir=out_dir)
            self.assertEqual(report.compat_mode.value, "legacy")
            self.assertIn(report.result.value, {"pass", "warn"})
            self.assertTrue((out_dir / "analysis_package.migrated.json").exists())


if __name__ == "__main__":
    unittest.main()
