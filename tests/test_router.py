import copy
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from jev_router import hook, judgments
from jev_router.config import DEFAULTS
from jev_router.judgments import Judgments, heuristic, parse_jev_response
from jev_router.router import decide, quantile_level, route, session_rank, should_delegate


def config():
    return copy.deepcopy(DEFAULTS)


def jev_payload(complexity, reasoning, risk, context=0.05):
    """A Jev response with the given per-level probabilities."""
    def score(probs):
        return {"type": "score", "score": sum(i * p for i, p in enumerate(probs)), "confidence": 0.9,
                "legend": {}, "probabilities": {str(i): p for i, p in enumerate(probs)}}
    return {
        "model": "jev-test",
        "answers": {
            "complexity": score(complexity),
            "reasoning": score(reasoning),
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
    def test_trivial_goes_to_cheapest(self):
        j = parse_jev_response(jev_payload([1, 0, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0]))
        self.assertEqual(decide(j, config()).name, "haiku-low")

    def test_small_edit(self):
        j = parse_jev_response(jev_payload([0, 1, 0, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0]))
        self.assertEqual(decide(j, config()).name, "sonnet-medium")

    def test_hardest_goes_to_top(self):
        j = parse_jev_response(jev_payload([0, 0, 0, 0, 1], [0, 0, 0, 1], [0, 0, 1, 0]))
        self.assertEqual(decide(j, config()).name, "opus-xhigh")

    def test_risk_floor(self):
        # Easy but touches production: never below opus-high.
        j = parse_jev_response(jev_payload([0, 1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 0, 1]))
        decision = decide(j, config())
        self.assertEqual(decision.name, "opus-high")
        self.assertIn("raised to risk floor", decision.reasons)

    def test_uncertainty_picks_stronger(self):
        sure = parse_jev_response(jev_payload([0, 0, 1, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0]))
        unsure = parse_jev_response(jev_payload([0, 0, 0.7, 0.3, 0], [0, 0, 0.7, 0.3], [0, 1, 0, 0]))
        tiers = [t["name"] for t in DEFAULTS["tiers"]]
        self.assertGreater(tiers.index(decide(unsure, config()).name), tiers.index(decide(sure, config()).name))

    def test_heuristic_bump(self):
        j = Judgments(source="heuristic", distributions={
            "complexity": [1, 0, 0, 0, 0], "reasoning": [1, 0, 0, 0], "risk": [1, 0, 0, 0]})
        self.assertEqual(decide(j, config()).name, "sonnet-medium")

    def test_thresholds_are_configurable(self):
        cfg = config()
        cfg["tiers"][0]["max_difficulty"] = 0.5
        j = parse_jev_response(jev_payload([0, 1, 0, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0]))
        self.assertEqual(decide(j, cfg).name, "haiku-low")


class HeuristicTest(unittest.TestCase):
    def tier(self, prompt):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}):
            return route(prompt, config()).name

    def test_simple_cheaper_than_complex(self):
        tiers = [t["name"] for t in DEFAULTS["tiers"]]
        simple = self.tier("what is a python decorator?")
        complex_ = self.tier(
            "Refactor the authentication module across the whole codebase to fix a race condition "
            "in token refresh, and redesign the session architecture so it scales.")
        self.assertLess(tiers.index(simple), tiers.index(complex_))
        self.assertIn(complex_, ("opus-high", "opus-xhigh"))

    def test_context_reference(self):
        self.assertGreaterEqual(heuristic("yes, do that").context_dependent, 0.5)
        self.assertLess(heuristic("add a --verbose flag to cli.py").context_dependent, 0.5)


class OverrideTest(unittest.TestCase):
    def test_off(self):
        self.assertIsNone(route("[jev:off] anything", config()))

    def test_force(self):
        decision = route("[jev:opus-xhigh] rename x", config())
        self.assertEqual((decision.name, decision.source), ("opus-xhigh", "override"))

    def test_unknown_tier_ignored(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": ""}):
            self.assertEqual(route("[jev:gpt] hi", config()).source, "heuristic")


class JevCallTest(unittest.TestCase):
    def test_uses_jev_when_key_set(self):
        body = json.dumps(jev_payload([1, 0, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0])).encode()
        response = mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(body)
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "k"}), \
                mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
            decision = route("hello", config())
        self.assertEqual((decision.source, decision.name), ("jev", "haiku-low"))
        sent = json.loads(urlopen.call_args[0][0].data)
        self.assertEqual(set(sent["questions"]), {"complexity", "reasoning", "risk", "context_dependent"})
        self.assertEqual(sent["state"]["request"]["prompt"], "hello")

    def test_falls_back_on_error(self):
        with mock.patch.dict(os.environ, {"TYPESAFE_API_KEY": "k"}), \
                mock.patch("urllib.request.urlopen", side_effect=OSError("down")):
            decision = route("hello", config())
        self.assertEqual(decision.source, "heuristic")
        self.assertIn("down", decision.detail["jev_error"])

    def test_bad_response_raises(self):
        with self.assertRaises(judgments.JevUnavailable):
            parse_jev_response({"answers": {}})


class DelegateTest(unittest.TestCase):
    def decision(self, tier, complexity_level=2, context=0.0):
        j = parse_jev_response(jev_payload([1, 0, 0, 0, 0], [1, 0, 0, 0], [1, 0, 0, 0], context))
        d = decide(j, config())
        d.tier = next(t for t in DEFAULTS["tiers"] if t["name"] == tier)
        d.complexity_level = complexity_level
        return d

    def test_upgrade(self):
        rank = session_rank("haiku-low", DEFAULTS["tiers"])
        self.assertTrue(should_delegate(self.decision("opus-high"), rank, config())[0])

    def test_same_tier_stays(self):
        rank = session_rank("sonnet-medium", DEFAULTS["tiers"])
        self.assertFalse(should_delegate(self.decision("sonnet-medium"), rank, config())[0])

    def test_trivial_not_delegated_down(self):
        rank = session_rank("claude-opus-5-5", DEFAULTS["tiers"])
        self.assertFalse(should_delegate(self.decision("haiku-low", complexity_level=0), rank, config())[0])

    def test_real_work_delegated_down(self):
        rank = session_rank("claude-opus-5-5", DEFAULTS["tiers"])
        self.assertTrue(should_delegate(self.decision("sonnet-medium", complexity_level=2), rank, config())[0])

    def test_context_dependent_stays(self):
        rank = session_rank("haiku-low", DEFAULTS["tiers"])
        self.assertFalse(should_delegate(self.decision("opus-high", context=0.9), rank, config())[0])

    def test_model_id_rank(self):
        self.assertEqual(session_rank("claude-sonnet-5-5", DEFAULTS["tiers"]), 2)
        self.assertIsNone(session_rank("unknown-model", DEFAULTS["tiers"]))


class HookTest(unittest.TestCase):
    def run_hook(self, payload, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            environ = {"TYPESAFE_API_KEY": "", "CLAUDE_PLUGIN_DATA": tmp, "HOME": tmp, **(env or {})}
            with mock.patch.dict(os.environ, environ):
                out = hook.handle(payload)
                log = os.path.join(tmp, "decisions.jsonl")
                logged = ""
                if os.path.exists(log):
                    with open(log) as f:
                        logged = f.read()
        return out, logged

    def test_output_shape_and_log(self):
        out, logged = self.run_hook({"prompt": "Refactor the payment service across all modules for production",
                                     "session_id": "s1"}, {"JEV_ROUTER_SESSION_TIER": "haiku-low"})
        ctx = out["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "UserPromptSubmit")
        self.assertIn('subagent_type "jev-router:opus-', ctx["additionalContext"])
        self.assertIn("jev-router:", out["systemMessage"])
        record = json.loads(logged)
        self.assertTrue(record["delegate"])
        self.assertNotIn("prompt", record)

    def test_skips_slash_commands_and_empty(self):
        self.assertEqual(self.run_hook({"prompt": "/help"})[0], None)
        self.assertEqual(self.run_hook({"prompt": "  "})[0], None)

    def test_transcript_model(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"type": "user", "message": {"role": "user", "content": "x"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5"}}) + "\n")
        try:
            self.assertEqual(hook.last_session_model(f.name), "claude-opus-5-5")
        finally:
            os.unlink(f.name)

    def test_main_never_fails(self):
        with mock.patch("sys.stdin", io.StringIO("not json")), mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(hook.main(), 0)


if __name__ == "__main__":
    unittest.main()
