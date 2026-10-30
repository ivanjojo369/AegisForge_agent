"""Offline unit tests for the Nebius HTTP adapter.

Repository location: tests/test_nexus/test_client.py
No real credentials, network calls or Nebius inference are used.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import requests

from aegisforge.nexus.nebius_client import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    NebiusAPIError,
    NebiusClient,
    NebiusConfigurationError,
)


TEST_KEY = "not-a-real-api-key-for-tests"


class NebiusClientTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(spec=requests.Session)
        self.client = NebiusClient(
            api_key=TEST_KEY,
            session=self.session,
            timeout_seconds=3,
        )

    def respond(self, body, *, status=200):
        response = Mock()
        response.status_code = status
        response.json.return_value = body
        self.session.request.return_value = response
        return response

    def test_constructing_adapter_does_not_call_network(self):
        self.assertEqual(self.client.base_url, DEFAULT_BASE_URL)
        self.assertEqual(self.client.model, DEFAULT_MODEL)
        self.session.request.assert_not_called()

    def test_missing_api_key_fails_without_network(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(NebiusConfigurationError):
                NebiusClient(session=self.session)
        self.session.request.assert_not_called()

    def test_http_url_rejected_before_credentials_sent(self):
        with self.assertRaises(NebiusConfigurationError):
            NebiusClient(api_key=TEST_KEY, base_url="http://localhost/v1", session=self.session)
        self.session.request.assert_not_called()

    def test_url_with_embedded_credentials_rejected(self):
        with self.assertRaises(NebiusConfigurationError):
            NebiusClient(api_key=TEST_KEY, base_url="https://a:b@example.com/v1", session=self.session)
        self.session.request.assert_not_called()

    def test_single_chat_request_and_normalized_response(self):
        self.respond({
            "id": "offline-req-001",
            "model": "offline-nemotron",
            "choices": [{"message": {"content": "Synthetic response"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 6, "completion_tokens": 3, "total_tokens": 9},
        })
        reply = self.client.chat([{"role": "user", "content": "Synthetic task"}], max_tokens=32)
        self.assertEqual(reply.content, "Synthetic response")
        self.assertEqual(reply.model, "offline-nemotron")
        self.assertEqual(reply.request_id, "offline-req-001")
        self.assertEqual(reply.usage["total_tokens"], 9)
        self.session.request.assert_called_once()
        args, kwargs = self.session.request.call_args
        self.assertEqual(args, ("POST", DEFAULT_BASE_URL + "/chat/completions"))
        self.assertEqual(kwargs["json"]["max_tokens"], 32)
        self.assertEqual(kwargs["json"]["messages"][0]["role"], "user")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer " + TEST_KEY)
        self.assertEqual(kwargs["timeout"], 3)

    def test_tool_calls_are_returned_but_never_executed(self):
        self.respond({
            "choices": [{
                "message": {"content": None, "tool_calls": [{
                    "id": "mock-call",
                    "type": "function",
                    "function": {"name": "unregistered_tool", "arguments": "{}"},
                }]},
                "finish_reason": "tool_calls",
            }]
        })
        reply = self.client.chat(
            [{"role": "user", "content": "List proposed tools"}],
            tools=[{"type": "function", "function": {"name": "unregistered_tool"}}],
        )
        self.assertEqual(reply.content, "")
        self.assertEqual(reply.tool_calls[0]["function"]["name"], "unregistered_tool")
        self.session.request.assert_called_once()  # No second request or tool execution.

    def test_empty_messages_rejected_before_network(self):
        with self.assertRaises(NebiusConfigurationError):
            self.client.chat([])
        self.session.request.assert_not_called()

    def test_invalid_role_rejected_before_network(self):
        with self.assertRaises(NebiusConfigurationError):
            self.client.chat([{"role": "root", "content": "x"}])
        self.session.request.assert_not_called()

    def test_invalid_tokens_rejected_before_network(self):
        for value in (0, -1, True):
            with self.subTest(value=value):
                with self.assertRaises(NebiusConfigurationError):
                    self.client.chat([{"role": "user", "content": "x"}], max_tokens=value)
        self.session.request.assert_not_called()

    def test_http_error_does_not_expose_private_response_body(self):
        response = self.respond({}, status=401)
        response.text = "private-provider-message: DO_NOT_LOG"
        with self.assertRaises(NebiusAPIError) as caught:
            self.client.chat([{"role": "user", "content": "test"}])
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("DO_NOT_LOG", str(caught.exception))
        self.assertNotIn(TEST_KEY, str(caught.exception))

    def test_timeout_never_triggers_automatic_retry(self):
        self.session.request.side_effect = requests.Timeout("secret diagnostic")
        with self.assertRaises(NebiusAPIError) as caught:
            self.client.chat([{"role": "user", "content": "test"}])
        self.assertEqual(self.session.request.call_count, 1)
        self.assertNotIn("secret diagnostic", str(caught.exception))

    def test_malformed_response_rejected(self):
        self.respond({"choices": []})
        with self.assertRaises(NebiusAPIError):
            self.client.chat([{"role": "user", "content": "test"}])

    def test_models_endpoint_lists_ids_without_inference(self):
        self.respond({"data": [{"id": "model-z"}, {"id": "model-a"}, {"other": "ignored"}]})
        self.assertEqual(self.client.list_model_ids(), ["model-a", "model-z"])
        args, _kwargs = self.session.request.call_args
        self.assertEqual(args, ("GET", DEFAULT_BASE_URL + "/models"))

    def test_external_session_is_not_closed_by_client(self):
        self.client.close()
        self.session.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
