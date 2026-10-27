import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from validate_analysis import validate


def sample_analysis():
    return {
        "version": "1.0",
        "meta": {
            "slug": "sample-decision",
            "title": "Sample decision",
            "question": "Act now or wait?",
            "as_of": "2026-10-07",
            "confidence": "medium",
            "equilibrium_status": "candidate",
            "formal_model": None,
        },
        "context": {
            "thread_intent": "Test the pipeline",
            "roots_considered": ["current thread"],
            "sources_used": ["thread"],
            "omissions": [],
            "stale_warnings": [],
            "redactions": [],
        },
        "decision": {
            "recommended_strategy_id": "S1",
            "recommendation": "Act with a reversible first step.",
            "rationale": "It gains information.",
            "next_step": "Run the small test.",
            "best_alternative": "Wait one day.",
            "main_downside": "Some rework.",
            "reversal_trigger": "The test becomes irreversible.",
            "stop_conditions": ["New high-risk evidence appears."],
            "human_gate": "The user approves the external action.",
        },
        "parameters": [
            {
                "id": "urgency",
                "label": "Urgency",
                "value": 50,
                "min": 0,
                "max": 100,
                "step": 5,
                "low_label": "Low",
                "high_label": "High",
            },
            {
                "id": "reversibility",
                "label": "Reversibility",
                "value": 70,
                "min": 0,
                "max": 100,
                "step": 5,
                "low_label": "Low",
                "high_label": "High",
            },
        ],
        "players": [
            {
                "id": "A1",
                "name": "User",
                "role": "decision-owner",
                "hidden": False,
                "objective": "Make a robust decision.",
                "constraints": ["Limited time"],
                "leverage": ["Can run a test"],
                "batna": "Wait.",
                "likely_move": "Run the test.",
            },
            {
                "id": "A2",
                "name": "Counterparty",
                "role": "counterparty",
                "hidden": False,
                "objective": "Preserve flexibility.",
                "constraints": ["Limited information"],
                "leverage": ["Can respond to the test"],
                "batna": "Maintain the current state.",
                "likely_move": "Respond after seeing the test result.",
            },
        ],
        "strategies": [
            {
                "id": "S1",
                "name": "Small test",
                "summary": "Take a reversible information-gaining step.",
                "base_fit": 75,
                "sensitivity": {"urgency": 0.2, "reversibility": 0.2},
                "tradeoffs": ["Some rework"],
                "conditions": ["The test stays reversible"],
                "next_response": "The counterpart supplies new information.",
                "reversibility": "reversible",
            },
            {
                "id": "S2",
                "name": "Wait",
                "summary": "Delay the decision.",
                "base_fit": 45,
                "sensitivity": {"urgency": -0.3, "reversibility": -0.1},
                "tradeoffs": ["Lost time"],
                "conditions": ["Waiting creates information"],
                "next_response": "The state remains unresolved.",
                "reversibility": "reversible",
            },
        ],
        "evidence": {
            "facts": [
                {
                    "id": "F1",
                    "claim": "A reversible test exists.",
                    "source_path": "thread",
                    "as_of": "2026-10-07",
                    "freshness": "current",
                }
            ],
            "inferences": [
                {
                    "id": "I1",
                    "claim": "The test improves information.",
                    "basis": "F1",
                    "confidence": "medium",
                    "falsifier": "The test yields no signal.",
                }
            ],
            "unknowns": [
                {
                    "id": "U1",
                    "question": "Will the test yield a signal?",
                    "impact": "medium",
                    "resolution": "Run the test.",
                }
            ],
        },
        "risks": [
            {
                "name": "Rework",
                "severity": "low",
                "likelihood": "medium",
                "mitigation": "Time-box the test.",
            }
        ],
        "response_tree": [
            {
                "if": "The test runs",
                "then": "New evidence arrives",
                "owner": "User",
                "checkpoint": "After the test",
            }
        ],
        "psychology": [
            {
                "pattern": "Analysis paralysis",
                "status": "inferred",
                "signal": "The decision is delayed.",
                "countermove": "Use a time-boxed test.",
            }
        ],
    }


class PipelineTests(unittest.TestCase):
    def test_sample_validates_and_renders_fragment(self):
        analysis = sample_analysis()
        self.assertEqual([], validate(analysis))
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            source = directory / "analysis.json"
            output = directory / "report.html"
            source.write_text(json.dumps(analysis), encoding="utf-8")
            subprocess.run(
                [sys.executable, str(SKILL_DIR / "scripts" / "render_report.py"), str(source), str(output)],
                check=True,
                capture_output=True,
                text=True,
            )
            fragment = output.read_text(encoding="utf-8")
            self.assertIn('id="nash-sample-decision"', fragment)
            self.assertNotIn("__NASH_", fragment)
            self.assertNotIn("<!doctype", fragment.lower())
            self.assertNotIn("<html", fragment.lower())

    def test_formal_claim_requires_model(self):
        analysis = sample_analysis()
        analysis["meta"]["equilibrium_status"] = "proven"
        self.assertIn(
            "meta.formal_model is required when equilibrium_status is 'proven'",
            validate(analysis),
        )

    def test_formal_claim_requires_complete_best_response_model(self):
        analysis = sample_analysis()
        analysis["meta"]["equilibrium_status"] = "proven"
        analysis["meta"]["formal_model"] = "trust me"
        self.assertTrue(validate(analysis))

        analysis["meta"]["formal_model"] = {
            "model_type": "pure-strategy-normal-form",
            "players": ["A1", "A2"],
            "strategies": {"A1": ["S1", "S2"], "A2": ["T1", "T2"]},
            "payoffs": [
                {"profile": ["S1", "T1"], "values": {"A1": 2, "A2": 2}},
                {"profile": ["S1", "T2"], "values": {"A1": 0, "A2": 1}},
                {"profile": ["S2", "T1"], "values": {"A1": 1, "A2": 0}},
                {"profile": ["S2", "T2"], "values": {"A1": 0, "A2": 0}},
            ],
            "equilibrium_profile": ["S1", "T1"],
            "best_response_checks": [
                {"actor_id": "A1", "profile": ["S1", "T1"], "profitable_deviation": "none"},
                {"actor_id": "A2", "profile": ["S1", "T1"], "profitable_deviation": "none"},
            ],
            "solver": "exhaustive pure-strategy check",
            "verified": True,
        }
        self.assertEqual([], validate(analysis))

        analysis["meta"]["formal_model"]["payoffs"][2]["values"]["A1"] = 3
        self.assertTrue(any("profitable unilateral deviation" in error for error in validate(analysis)))

        analysis["meta"]["formal_model"]["payoffs"] = [{}]
        self.assertTrue(validate(analysis))

    def test_rejects_private_identifiers_and_absolute_home_paths(self):
        analysis = sample_analysis()
        analysis["context"]["sources_used"] = ["/Users/example/private.md"]
        analysis["evidence"]["facts"][0]["claim"] = "Contact jane@example.com about account 123456789."
        errors = validate(analysis)
        self.assertTrue(any("absolute home path" in error for error in errors))
        self.assertTrue(any("email address" in error for error in errors))
        self.assertTrue(any("raw long account" in error for error in errors))

    def test_rejects_unknown_schema_version_and_confidence(self):
        analysis = sample_analysis()
        analysis["version"] = "99"
        analysis["meta"]["confidence"] = "certain"
        errors = validate(analysis)
        self.assertIn("version: only schema version '1.0' is supported", errors)
        self.assertIn("meta.confidence: expected low, medium, or high", errors)


if __name__ == "__main__":
    unittest.main()
