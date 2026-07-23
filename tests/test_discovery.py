from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch
from pathlib import Path

from ai_usage_tracker.discovery import discover_provider
from ai_usage_tracker.manifest import ProviderManifest


class DiscoveryTests(TestCase):
    def test_does_not_expose_resolved_installation_path(self) -> None:
        provider = ProviderManifest(
            id="example",
            display_name="Example",
            stability="test",
            enabled_by_default=False,
            permissions=frozenset({"local_metadata"}),
            credential_access="none",
            network_hosts=(),
            executables=("example-cli",),
            paths=(),
            executable_role="provider",
            notes="test",
        )
        with patch("ai_usage_tracker.discovery.shutil.which", return_value="/private/user/bin/example-cli"):
            result = discover_provider(provider)

        self.assertTrue(result.detected)
        self.assertFalse(result.host_detected)
        self.assertEqual(result.executable, "example-cli")
        self.assertNotIn("/private", str(result.to_dict()))

    def test_host_application_does_not_imply_provider_detection(self) -> None:
        provider = ProviderManifest(
            id="extension",
            display_name="Extension",
            stability="test",
            enabled_by_default=False,
            permissions=frozenset({"local_metadata"}),
            credential_access="none",
            network_hosts=(),
            executables=("host-editor",),
            paths=(),
            executable_role="host",
            notes="test",
        )
        with patch("ai_usage_tracker.discovery.shutil.which", return_value="/usr/bin/host-editor"):
            result = discover_provider(provider)

        self.assertFalse(result.detected)
        self.assertTrue(result.host_detected)
        self.assertEqual(result.detection_confidence, "host_only")

    def test_codex_discovery_uses_standard_install_locations(self) -> None:
        provider = ProviderManifest(
            id="codex",
            display_name="Codex",
            stability="experimental",
            enabled_by_default=False,
            permissions=frozenset({"local_metadata", "local_process"}),
            credential_access="delegated_to_official_process",
            network_hosts=(),
            executables=("codex",),
            paths=(),
            executable_role="provider",
            notes="test",
        )
        with (
            patch("ai_usage_tracker.discovery.shutil.which", return_value=None),
            patch(
                "ai_usage_tracker.discovery.resolve_codex_executable",
                return_value=Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
            ),
        ):
            result = discover_provider(provider)

        self.assertTrue(result.detected)
        self.assertEqual(result.executable, "codex")
        self.assertNotIn("/Applications", str(result.to_dict()))


if __name__ == "__main__":
    import unittest

    unittest.main()
