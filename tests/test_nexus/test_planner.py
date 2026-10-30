"""Offline unit tests: no Nebius credentials or network calls required."""

import json
import unittest

from aegisforge.nexus.nebius_client import NebiusReply
from aegisforge.nexus.planner import NexusPlanner, NexusPlanningError


VALID_PLAN = {
    "goal": "Implement a reliable API endpoint",
    "steps": [
        {
            "id": "s1", "kind": "reason", "title": "Define contract",
            "description": "Describe inputs, outputs and error cases",
            "tool": None, "depends_on": [],
            "success_criteria": "A documented input/output contract exists",
        },
        {
            "id": "s2", "kind": "tool", "title": "Review files",
            "description": "Inspect files relevant to the service",
            "tool": "read_file", "depends_on": ["s1"],
            "success_criteria": "Relevant implementation files are identified",
        },
        {
            "id": "s3", "kind": "verify", "title": "Check evidence",
            "description": "Verify implementation against expected behavior",
            "tool": None, "depends_on": ["s1", "s2"],
            "success_criteria": "Test evidence supports expected inputs and outputs",
        },
    ],
    "assumptions": ["The project has a test environment"],
    "risks": ["Incorrect input validation"],
}


class FakeClient:
    def __init__(self, payload=None, *, finish_reason="stop", tool_calls=()):
        self.payload = VALID_PLAN if payload is None else payload
        self.finish_reason = finish_reason
        self.tool_calls = tool_calls
        self.calls = []

    def chat(self, messages, *, max_tokens=512, temperature=0.2):
        self.calls.append({"messages": messages, "max_tokens": max_tokens,
                           "temperature": temperature})
        return NebiusReply(
            content=(self.payload if isinstance(self.payload, str)
                     else json.dumps(self.payload)),
            model="fake-nemotron",
            finish_reason=self.finish_reason,
            usage={"total_tokens": 137},
            request_id="fake-request-id",
            tool_calls=tuple(self.tool_calls),
        )


class NexusPlannerTests(unittest.TestCase):
    def create_planner(self, payload=None, **kwargs):
        client = FakeClient(payload, **kwargs)
        return NexusPlanner(client), client

    def test_valid_plan_with_allowed_tool_and_metadata(self):
        planner, client = self.create_planner()
        result = planner.plan("Create an API", allowed_tools={"read_file": "Read sandbox file"})
        self.assertEqual(result.steps[1].tool, "read_file")
        self.assertEqual(result.steps[-1].kind, "verify")
        self.assertEqual(result.model, "fake-nemotron")
        self.assertEqual(result.usage["total_tokens"], 137)
        self.assertEqual(len(client.calls), 1)
        self.assertIsNone(client.calls[0]["temperature"])
        self.assertEqual(result.to_dict()["steps"][0]["id"], "s1")
        json.dumps(result.to_dict())

    def test_does_not_call_model_for_invalid_request(self):
        planner, client = self.create_planner()
        with self.assertRaises(NexusPlanningError):
            planner.plan("  ")
        self.assertEqual(len(client.calls), 0)

    def test_rejects_unknown_tool(self):
        planner, client = self.create_planner()
        with self.assertRaisesRegex(NexusPlanningError, "unapproved tool"):
            planner.plan("Create an API", allowed_tools={"run_tests": "Run sandbox tests"})
        self.assertEqual(len(client.calls), 1)

    def test_rejects_unallowlisted_tool_when_none_allowed(self):
        planner, client = self.create_planner()
        with self.assertRaisesRegex(NexusPlanningError, "unapproved tool"):
            planner.plan("Create an API")
        self.assertEqual(len(client.calls), 1)

    def test_rejects_forward_dependency(self):
        invalid = json.loads(json.dumps(VALID_PLAN))
        invalid["steps"][0]["depends_on"] = ["s2"]
        planner, _ = self.create_planner(invalid)
        with self.assertRaisesRegex(NexusPlanningError, "dependencies"):
            planner.plan("Create an API", allowed_tools={"read_file": "Read files"})

    def test_rejects_missing_final_verifier(self):
        invalid = json.loads(json.dumps(VALID_PLAN))
        invalid["steps"][-1]["kind"] = "reason"
        planner, _ = self.create_planner(invalid)
        with self.assertRaisesRegex(NexusPlanningError, "final step"):
            planner.plan("Create an API", allowed_tools={"read_file": "Read files"})

    def test_rejects_nonsequential_step_ids(self):
        invalid = json.loads(json.dumps(VALID_PLAN))
        invalid["steps"][1]["id"] = "s7"
        planner, _ = self.create_planner(invalid)
        with self.assertRaisesRegex(NexusPlanningError, "s2"):
            planner.plan("Create an API", allowed_tools={"read_file": "Read files"})

    def test_rejects_model_tool_calls(self):
        planner, _ = self.create_planner(tool_calls=({"type": "function"},))
        with self.assertRaisesRegex(NexusPlanningError, "tool calls"):
            planner.plan("Create an API")

    def test_rejects_truncated_completion(self):
        planner, _ = self.create_planner(finish_reason="length")
        with self.assertRaisesRegex(NexusPlanningError, "truncated"):
            planner.plan("Create an API")

    def test_accepts_single_json_fence(self):
        response = "```json\n" + json.dumps(VALID_PLAN) + "\n```"
        planner, _ = self.create_planner(response)
        result = planner.plan("Create an API", allowed_tools={"read_file": "Read files"})
        self.assertEqual(len(result.steps), 3)

    def test_rejects_non_json(self):
        planner, _ = self.create_planner("A plan is good")
        with self.assertRaisesRegex(NexusPlanningError, "valid JSON"):
            planner.plan("Create an API")

    def test_rejects_too_many_steps(self):
        planner, _ = self.create_planner()
        planner = NexusPlanner(planner.client, max_steps=2)
        with self.assertRaisesRegex(NexusPlanningError, "steps must"):
            planner.plan("Create an API", allowed_tools={"read_file": "Read files"})


if __name__ == "__main__":
    unittest.main()
