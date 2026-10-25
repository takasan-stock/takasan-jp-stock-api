import unittest
from unittest.mock import patch

from main import health
from v3_contracts import V3_CONTRACT_VERSION, V3_ENGINE_MODULES


class V3ReleaseCandidateTests(unittest.TestCase):
    def test_v3_contract_and_all_engine_modules_are_traceable(self):
        self.assertEqual(V3_CONTRACT_VERSION, "v3-contract-1")
        self.assertEqual(
            set(V3_ENGINE_MODULES),
            {
                "action_status", "priority_board", "deep_fcf", "multiple_expansion",
                "me_daily", "short_cover", "entry", "pre_trade",
            },
        )
        self.assertTrue(all(len(value) == 40 for value in V3_ENGINE_MODULES.values()))

    @patch.dict("os.environ", {"RENDER_GIT_COMMIT": "render-rc-sha"}, clear=False)
    def test_health_exposes_release_tracking_without_changing_legacy_version(self):
        result = health()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["version"], "1.0.0")
        self.assertEqual(result["release"]["render_commit"], "render-rc-sha")
        self.assertEqual(result["release"]["v3_contract_version"], "v3-contract-1")
        self.assertEqual(result["release"]["v3_engine_modules"], V3_ENGINE_MODULES)


if __name__ == "__main__":
    unittest.main()
