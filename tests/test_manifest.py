from __future__ import annotations

import unittest

from ai_usage_tracker.manifest import load_manifest


class ManifestTests(unittest.TestCase):
    def test_all_providers_are_default_deny(self) -> None:
        providers = load_manifest()
        self.assertGreater(len(providers), 0)
        self.assertTrue(all(not provider.enabled_by_default for provider in providers))

    def test_private_provider_is_marked_experimental(self) -> None:
        providers = {provider.id: provider for provider in load_manifest()}
        cursor = providers["cursor"]
        self.assertEqual(cursor.stability, "private_experimental")
        self.assertIn("private_state", cursor.permissions)

    def test_hosts_require_provider_network_permission(self) -> None:
        for provider in load_manifest():
            if provider.network_hosts:
                self.assertIn("provider_network", provider.permissions)

    def test_every_local_state_adapter_declares_its_exact_paths(self) -> None:
        providers = {provider.id: provider for provider in load_manifest()}

        for provider_id in (
            "claude",
            "cursor",
            "devin",
            "antigravity",
            "github_copilot",
        ):
            self.assertTrue(providers[provider_id].paths)


if __name__ == "__main__":
    unittest.main()
