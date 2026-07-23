from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from ai_usage_tracker.security import (
    absolute_environment_path,
    redact,
    validate_official_url,
    validate_redirect,
)


class RedactionTests(unittest.TestCase):
    def test_redacts_sensitive_keys_and_token_patterns(self) -> None:
        value = {
            "authorization": "Bearer CANARY_SECRET_AUTHORIZATION",
            "nested": {
                "token": "CANARY_SECRET_NESTED",
                "message": "failed with github_pat_CANARY_SECRET_VISIBLE",
            },
        }
        serialized = json.dumps(redact(value))
        self.assertNotIn("CANARY_SECRET", serialized)
        self.assertGreaterEqual(serialized.count("[REDACTED]"), 3)

    def test_redacts_provider_specific_keys_and_jwts(self) -> None:
        value = {
            "cursorAuth/accessToken": "CANARY_SECRET_CURSOR",
            "message": (
                "failed with "
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJDQU5BUllfU0VDUkVUIn0."
                "c2lnbmF0dXJlMTIz"
            ),
        }

        serialized = json.dumps(redact(value))

        self.assertNotIn("CANARY_SECRET", serialized)
        self.assertNotIn("eyJ", serialized)

    def test_ignores_relative_environment_directories(self) -> None:
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "relative/config"}):
            self.assertIsNone(absolute_environment_path("XDG_CONFIG_HOME"))


class NetworkPolicyTests(unittest.TestCase):
    def test_accepts_exact_official_https_host(self) -> None:
        self.assertEqual(
            validate_official_url("https://api.github.com/copilot", ("api.github.com",)),
            "api.github.com",
        )

    def test_rejects_subdomain_lookalike(self) -> None:
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            validate_official_url(
                "https://api.github.com.attacker.example/copilot",
                ("api.github.com",),
            )

    def test_rejects_credentials_in_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "credentials"):
            validate_official_url(
                "https://token@api.github.com/copilot",
                ("api.github.com",),
            )

    def test_rejects_cross_host_redirect(self) -> None:
        with self.assertRaisesRegex(ValueError, "cross-host"):
            validate_redirect(
                "https://api.github.com/usage",
                "https://github.com/login",
                ("api.github.com", "github.com"),
            )


if __name__ == "__main__":
    unittest.main()
