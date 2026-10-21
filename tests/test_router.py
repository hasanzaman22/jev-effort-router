import copy
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from jev_router import command, config as config_module, hook, judgments
from jev_router.config import DEFAULTS
from jev_router.judgments import Judgments, heuristic, parse_jev_response
from jev_router.router import decide, quantile_level, route


# Clear every variable a key can come from, so a key in the developer's shell
# never reaches a test.
NO_KEYS = {name: "" for name in ("JEV_API_KEY", *judgments.KEY_ENV_FALLBACKS)}


def config():
    return copy.deepcopy(DEFAULTS)


def jev_payload(effort, risk, context=0.05):
    """A Jev response with the given per-level probabilities."""
    def score(probs):
        return {"type": "score", "score": sum(i * p for i, p in enumerate(probs)), "confidence": 0.9,
                "legend": {}, "probabilities": {str(i): p for i, p in enumerate(probs)}}
    return {
        "model": "jev-test",
        "answers": {
            "effort": score(effort),
            "risk": score(risk),
            "context_dependent": {"type": "noul", "noul": context},
        },
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class QuantileTest(unittest.TestCase):
    def test_certain(self):
        self.assertEqual(quantile_level([0, 1, 0], 0.85), 1)

    def test_uncertain_resolves_up(self):
        # Mean is 1.3, but 30% sits on level 2, so the 0.85 quantile is level 2.
        self.assertEqual(quantile_level([0, 0.7, 0.3], 0.85), 2)

    def test_mostly_low(self):
        self.assertEqual(quantile_level([0.9, 0.1, 0], 0.85), 0)


class DecideTest(unittest.TestCase):
    def test_trivial_gets_low(self):
        j = parse_jev_response(jev_payload([1, 0, 0, 0], [1, 0, 0, 0]))
        self.assertEqual(decide(j, config()).name, "low")

    def test_moderate_gets_medium(self):
        j = parse_jev_response(jev_payload([0, 1, 0, 0], [0, 1, 0, 0]))
        self.assertEqual(decide(j, config()).name, "medium")

    def test_hardest_gets_xhigh(self):
        j = parse_jev_response(jev_payload([0, 0, 0, 1], [0, 0, 1, 0]))
        self.assertEqual(decide(j, config()).name, "xhigh")

    def test_risk_floor(self):
        # Easy but touches production: never below high effort.
        j = parse_jev_response(jev_payload([0, 1, 0, 0], [0, 0, 0, 1]))
        decision = decide(j, config())
        self.assertEqual(decision.name, "high")
        self.assertIn("raised to risk floor", decision.reasons)

    def test_uncertainty_picks_more_effort(self):
        sure = parse_jev_response(jev_payload([0, 1, 0, 0], [0, 1, 0, 0]))
        unsure = parse_jev_response(jev_payload([0, 0.7, 0.3, 0], [0, 1, 0, 0]))
        self.assertEqual((decide(sure, config()).name, decide(unsure, config()).name), ("medium", "high"))

    def test_heuristic_bump(self):
        j = Judgments(source="heuristic", distributions={"effort": [1, 0, 0, 0], "risk": [1, 0, 0, 0]})
        self.assertEqual(decide(j, config()).name, "medium")

    def test_thresholds_are_configurable(self):
        cfg = config()
        cfg["tiers"][0]["max_difficulty"] = 0.34
        j = parse_jev_response(jev_payload([0, 1, 0, 0], [0, 1, 0, 0]))
        self.assertEqual(decide(j, cfg).name, "low")


class HeuristicTest(unittest.TestCase):
    def tier(self, prompt):
        with mock.patch.dict(os.environ, NO_KEYS):
            return route(prompt, config()).name

    def test_simple_cheaper_than_complex(self):
        tiers = [t["name"] for t in DEFAULTS["tiers"]]
        simple = self.tier("what is a python decorator?")
        complex_ = self.tier(
            "Refactor the authentication module across the whole codebase to fix a race condition "
            "in token refresh, and redesign the session architecture so it scales.")
        self.assertLess(tiers.index(simple), tiers.index(complex_))
        self.assertIn(complex_, ("high", "xhigh"))

    def test_context_reference(self):
        self.assertGreaterEqual(heuristic("yes, do that").context_dependent, 0.5)
        self.assertLess(heuristic("add a --verbose flag to cli.py").context_dependent, 0.5)


class OverrideTest(unittest.TestCase):
    def test_off(self):
        self.assertIsNone(route("[jev:off] anything", config()))

    def test_force(self):
        decision = route("[jev:xhigh] rename x", config())
        self.assertEqual((decision.name, decision.source), ("xhigh", "override"))

    def test_unknown_tier_ignored(self):
        with mock.patch.dict(os.environ, NO_KEYS):
            self.assertEqual(route("[jev:gpt] hi", config()).source, "heuristic")


class JevCallTest(unittest.TestCase):
    def test_uses_jev_when_key_set(self):
        body = json.dumps(jev_payload([1, 0, 0, 0], [1, 0, 0, 0])).encode()
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(body)
        with mock.patch.dict(os.environ, {**NO_KEYS, "TYPESAFE_API_KEY": "k"}), \
                mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            decision = route("hello", config())
        self.assertEqual((decision.source, decision.name), ("jev", "low"))
        sent = json.loads(urlopen.call_args[0][0].data)
        self.assertEqual(set(sent["questions"]), {"effort", "risk", "context_dependent"})
        self.assertEqual(sent["state"]["request"]["prompt"], "hello")

    def test_falls_back_on_error(self):
        with mock.patch.dict(os.environ, {**NO_KEYS, "TYPESAFE_API_KEY": "k"}), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            decision = route("hello", config())
        self.assertEqual(decision.source, "heuristic")
        self.assertIn("down", decision.detail["jev_error"])

    def test_bad_response_raises(self):
        with self.assertRaises(judgments.JevUnavailable):
            parse_jev_response({"answers": {}})


class ProviderTest(unittest.TestCase):
    def resolve(self, env, **jev):
        with mock.patch.dict(os.environ, {**NO_KEYS, **env}):
            return judgments.resolve_jev({**config()["jev"], **jev})

    def test_prefix_picks_provider(self):
        cases = {
            "sk-or-v1-abc": ("openrouter", "https://openrouter.ai/api/v1/systemone", "~typesafe/jev-latest"),
            "oc_sk_abc": ("opencode", "https://opencode.ai/inference/systemone/v1/systemone", "jev-1.13-free"),
            "ts-abc": ("typesafe", "https://api.typesafe.ai/v1/systemone", "jev-latest"),
        }
        for key, expected in cases.items():
            self.assertEqual(self.resolve({"JEV_API_KEY": key})[1:], expected)

    def test_fallback_env_names(self):
        key, provider, _, _ = self.resolve({"OPENROUTER_API_KEY": "sk-or-v1-abc"})
        self.assertEqual((key, provider), ("sk-or-v1-abc", "openrouter"))

    def test_jev_api_key_wins(self):
        self.assertEqual(self.resolve({"JEV_API_KEY": "oc_sk_a", "OPENROUTER_API_KEY": "sk-or-b"})[1], "opencode")

    def test_explicit_config_wins(self):
        _, provider, endpoint, model = self.resolve({"JEV_API_KEY": "sk-or-v1-abc"}, model="typesafe/jev-1.14")
        self.assertEqual((provider, model), ("openrouter", "typesafe/jev-1.14"))
        self.assertEqual(self.resolve({"JEV_API_KEY": "x"}, endpoint="http://localhost:1")[2], "http://localhost:1")

    def test_request_goes_to_detected_provider(self):
        body = json.dumps(jev_payload([1, 0, 0, 0], [1, 0, 0, 0])).encode()
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(body)
        with mock.patch.dict(os.environ, {**NO_KEYS, "JEV_API_KEY": "sk-or-v1-abc"}), \
                mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            decision = route("hello", config())
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/systemone")
        self.assertEqual(request.get_header("Authorization"), "Bearer sk-or-v1-abc")
        self.assertEqual(json.loads(request.data)["model"], "~typesafe/jev-latest")
        self.assertEqual(decision.detail["provider"], "openrouter")


class SwitchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patcher = mock.patch.dict(os.environ, {**NO_KEYS, "HOME": self.tmp.name, "JEV_ROUTER_CONFIG": ""})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_command(self, *args):
        with mock.patch("sys.stdout", io.StringIO()) as out:
            code = command.main(list(args))
        return code, out.getvalue()

    def test_disable_persists_until_enable(self):
        self.assertTrue(config_module.load_config(None)["enabled"])
        self.assertEqual(self.run_command("disable")[0], 0)
        self.assertFalse(config_module.load_config(None)["enabled"])
        self.assertIsNone(hook.handle_switch({"prompt": "add pagination to the users endpoint"}))
        self.run_command("enable")
        self.assertTrue(config_module.load_config(None)["enabled"])
        self.assertIsNotNone(hook.handle_switch({"prompt": "add pagination to the users endpoint"}))

    def test_state_overrides_config_file(self):
        path = os.path.join(self.tmp.name, "cfg.json")
        with open(path, "w") as f:
            json.dump({"enabled": False}, f)
        with mock.patch.dict(os.environ, {"JEV_ROUTER_CONFIG": path}):
            self.assertFalse(config_module.load_config(None)["enabled"])
            self.run_command("enable")
            self.assertTrue(config_module.load_config(None)["enabled"])

    def test_status_and_usage(self):
        code, out = self.run_command("status")
        self.assertEqual(code, 0)
        self.assertIn("enabled (default)", out)
        self.assertIn("offline heuristic", out)
        self.assertEqual(self.run_command("bogus")[0], 2)


class HookTest(unittest.TestCase):
    def run_hook(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {**NO_KEYS, "CLAUDE_PLUGIN_DATA": tmp, "HOME": tmp,
                                              "JEV_ROUTER_CONFIG": ""}):
                out = hook.handle_switch(payload)
                log = os.path.join(tmp, "decisions.jsonl")
                logged = ""
                if os.path.exists(log):
                    with open(log) as f:
                        logged = f.read()
        return out, logged

    def test_output_and_log(self):
        out, logged = self.run_hook({"prompt": "Refactor the payment service across all modules for production",
                                     "session_id": "s1"})
        self.assertNotIn("model", out)
        self.assertIn(out["effort"], ("high", "xhigh"))
        self.assertFalse(out["keep"])
        self.assertTrue(out["message"].startswith("effort "))
        record = json.loads(logged)
        self.assertEqual(record["tier"], out["effort"])
        self.assertNotIn("prompt", record)

    def test_follow_up_keeps_effort(self):
        self.assertTrue(self.run_hook({"prompt": "yes, do that"})[0]["keep"])

    def test_override_is_never_kept(self):
        out = self.run_hook({"prompt": "[jev:high] continue"})[0]
        self.assertEqual((out["effort"], out["keep"]), ("high", False))

    def test_skips_slash_commands_and_empty(self):
        self.assertIsNone(self.run_hook({"prompt": "/help"})[0])
        self.assertIsNone(self.run_hook({"prompt": "  "})[0])
        self.assertIsNone(self.run_hook({"prompt": "[jev:off] hi"})[0])

    def test_main_never_fails(self):
        with mock.patch("sys.stdin", io.StringIO("not json")), mock.patch("sys.stderr", io.StringIO()), \
                mock.patch("sys.stdout", io.StringIO()) as out:
            self.assertEqual(hook.main_switch(), 0)
        self.assertEqual(out.getvalue(), "null")


if __name__ == "__main__":
    unittest.main()
