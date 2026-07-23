from __future__ import annotations

import json
import unittest

from ai_usage_tracker.fixtures import all_snapshots
from ai_usage_tracker.verification import verify_ui_readiness


class VerificationTests(unittest.TestCase):
    def test_all_fixtures_satisfy_ui_contract_without_canary_leaks(self) -> None:
        verification = verify_ui_readiness(all_snapshots())

        self.assertTrue(verification["ui_ready"])
        self.assertEqual(verification["snapshot_count"], 6)
        self.assertTrue(all(item["ready"] for item in verification["providers"]))
        self.assertNotIn("CANARY_SECRET", json.dumps(verification))

    def test_fixture_snapshots_do_not_serialize_canaries(self) -> None:
        serialized = json.dumps([snapshot.to_dict() for snapshot in all_snapshots()])
        self.assertNotIn("CANARY_SECRET", serialized)


if __name__ == "__main__":
    unittest.main()
