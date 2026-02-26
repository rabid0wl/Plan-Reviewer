from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.utils.io_json import canonical_json_dumps, write_json_atomic


class IoJsonTests(unittest.TestCase):
    def test_canonical_json_dumps_is_deterministic(self) -> None:
        a = {"b": 2, "a": 1}
        b = {"a": 1, "b": 2}
        self.assertEqual(canonical_json_dumps(a), canonical_json_dumps(b))

    def test_write_json_atomic_keeps_existing_file_on_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "payload.json"
            path.write_text(json.dumps({"old": True}), encoding="utf-8")

            with patch("src.utils.io_json.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    write_json_atomic(path, {"new": True}, indent=2, sort_keys=True)

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload, {"old": True})


if __name__ == "__main__":
    unittest.main()
