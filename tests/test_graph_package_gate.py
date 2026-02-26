from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.extraction.package_contract import (
    AnalysisValidationReport,
    CompatMode,
    ValidationQuality,
    ValidationResult,
)
from src.extraction.schemas import TileExtraction
from src.graph import assembly


def _report(*, result: str, warnings: list[str] | None = None, critical: list[str] | None = None) -> AnalysisValidationReport:
    return AnalysisValidationReport(
        contract_version="preanalysis.v1",
        run_id="run-1",
        validated_at="2026-01-01T00:00:00Z",
        result=ValidationResult(result),
        compat_mode=CompatMode.NATIVE,
        critical_errors=critical or [],
        warnings=warnings or [],
        quality=ValidationQuality(
            bad_ratio=0.0,
            warn_threshold=0.15,
            fail_threshold=0.30,
            paired_tiles=1,
            sanitized_tiles=0,
            skipped_low_coherence=0,
        ),
        package_manifest_path=None,
        migrated_manifest_path=None,
    )


class GraphPackageGateTests(unittest.TestCase):
    def test_graph_assembly_exits_when_package_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_path = root / "graph.json"
            argv = [
                "assembly.py",
                "--extractions-dir",
                str(root),
                "--utility-type",
                "SD",
                "--out",
                str(out_path),
            ]
            with patch("src.graph.assembly.validate_extraction_package", return_value=_report(result="fail", critical=["bad"])):
                with patch.object(sys, "argv", argv):
                    with self.assertRaises(SystemExit) as exc:
                        assembly.main()
            self.assertEqual(exc.exception.code, 2)
            self.assertFalse(out_path.exists())

    def test_graph_assembly_propagates_package_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            out_path = root / "graph.json"
            argv = [
                "assembly.py",
                "--extractions-dir",
                str(root),
                "--utility-type",
                "SD",
                "--out",
                str(out_path),
            ]
            extraction = TileExtraction.model_validate(
                {
                    "tile_id": "p1_r0_c0",
                    "page_number": 1,
                    "sheet_type": "plan_view",
                    "utility_types_present": ["SD"],
                    "structures": [],
                    "pipes": [],
                    "callouts": [],
                    "street_names": [],
                    "lot_numbers": [],
                }
            )
            with patch("src.graph.assembly.validate_extraction_package", return_value=_report(result="warn", warnings=["quality warning"])):
                with patch("src.graph.assembly.load_extractions_with_meta", return_value=([extraction], {})):
                    with patch.object(sys, "argv", argv):
                        assembly.main()

            payload = json.loads(out_path.read_text(encoding="utf-8"))
            warnings = payload.get("quality_summary", {}).get("warnings", [])
            self.assertIn("Package validation: quality warning", warnings)


if __name__ == "__main__":
    unittest.main()
