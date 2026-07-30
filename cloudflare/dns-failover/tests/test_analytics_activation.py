from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "enable_analytics_engine.py"
)
SPEC = importlib.util.spec_from_file_location("enable_analytics_engine", SCRIPT)
assert SPEC and SPEC.loader
activation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(activation)


def subscription() -> dict:
    return {
        "id": "must-not-appear",
        "price": 0,
        "state": "Paid",
        "rate_plan": {
            "id": "beta_analytics_engine_api",
            "public_name": "Analytics Engine",
        },
        "client_secrets": ["must-not-appear"],
    }


class AnalyticsEngineActivationTests(unittest.TestCase):
    def test_existing_subscription_is_idempotent_and_value_free(self):
        response = {"success": True, "result": [subscription()]}
        with patch.object(activation, "request_json", side_effect=[response, response]) as request:
            proof, result = activation.activate("https://example.invalid", "hidden")

        self.assertEqual(result, 0)
        self.assertFalse(proof["activation_performed"])
        self.assertEqual(request.call_count, 2)
        self.assertNotIn("must-not-appear", str(proof))
        self.assertFalse(proof["dns_or_worker_state_changed"])

    def test_missing_subscription_is_created_at_zero_price(self):
        created = {"success": True, "result": subscription()}
        after = {"success": True, "result": [subscription()]}
        with patch.object(
            activation,
            "request_json",
            side_effect=[{"success": True, "result": []}, created, after],
        ) as request:
            proof, result = activation.activate("https://example.invalid", "hidden")

        self.assertEqual(result, 0)
        self.assertTrue(proof["activation_performed"])
        self.assertEqual(request.call_count, 3)
        create_call = request.call_args_list[1]
        self.assertEqual(create_call.kwargs["method"], "POST")
        self.assertEqual(create_call.kwargs["payload"]["price"], 0)
        self.assertEqual(
            create_call.kwargs["payload"]["rate_plan"]["id"],
            "beta_analytics_engine_api",
        )

    def test_nonzero_price_fails_closed(self):
        paid = subscription()
        paid["price"] = 1
        response = {"success": True, "result": [paid]}
        with patch.object(activation, "request_json", side_effect=[response, response]):
            proof, result = activation.activate("https://example.invalid", "hidden")

        self.assertEqual(result, 1)
        self.assertEqual(proof["status"], "fail")

    def test_api_semantic_failure_stops_before_activation(self):
        with patch.object(
            activation,
            "request_json",
            return_value={"success": False, "errors": [{"message": "hidden"}]},
        ) as request:
            with self.assertRaises(activation.ActivationError):
                activation.activate("https://example.invalid", "hidden")

        self.assertEqual(request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
