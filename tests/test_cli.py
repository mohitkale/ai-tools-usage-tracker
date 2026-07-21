from __future__ import annotations

import unittest

from ai_usage_tracker.cli import _safe_error_message


class CliErrorTests(unittest.TestCase):
    def test_os_error_does_not_expose_local_path(self) -> None:
        error = PermissionError(13, "denied", "/Users/private-user/.codex/auth.json")

        message = _safe_error_message(error)

        self.assertNotIn("/Users", message)
        self.assertNotIn("auth.json", message)
        self.assertEqual(message, "A local filesystem or process operation failed.")

    def test_controlled_error_still_redacts_canary(self) -> None:
        message = _safe_error_message(ValueError("bad CANARY_SECRET_ERROR_VALUE"))
        self.assertNotIn("CANARY_SECRET", message)


if __name__ == "__main__":
    unittest.main()

