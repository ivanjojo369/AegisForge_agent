"""Tests for Nexus config: offline only; no Nebius HTTP calls."""

import json
import math
import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import requests

from aegisforge.nexus.config import NexusConfig, NexusConfigError, NexusSettings
from aegisforge.nexus.executor import NexusExecutor
from aegisforge.nexus.nebius_client import DEFAULT_BASE_URL, DEFAULT_MODEL, NebiusClient
from aegisforge.nexus.orchestrator import NexusOrchestrator
from aegisforge.nexus.planner import NexusPlanner


class FakeClient:
    def chat(self, messages, *, max_tokens=512, temperature=0.2):
        raise AssertionError("No inference should run when building config.")


class ConfigTests(unittest.TestCase):
    def test_defaults_work_without_key_for_offline_configuration(self):
        config = NexusConfig.from_env({})
        self.assertFalse(config.has_api_key)
        self.assertEqual(config.base_url, DEFAULT_BASE_URL)
        self.assertEqual(config.model, DEFAULT_MODEL)
        self.assertEqual(config.planner_max_steps, 6)

    def test_real_client_requires_key(self):
        with self.assertRaisesRegex(NexusConfigError, "NEBIUS_API_KEY"):
            NexusConfig.from_env({}).build_client()
        with self.assertRaisesRegex(NexusConfigError, "NEBIUS_API_KEY"):
            NexusConfig.from_env({}, require_api_key=True)

    def test_blank_key_is_treated_as_missing(self):
        self.assertFalse(NexusConfig.from_env({"NEBIUS_API_KEY": "  "}).has_api_key)

    def test_require_key_accepts_nonempty_key(self):
        config = NexusConfig.from_env({"NEBIUS_API_KEY": " abc "}, require_api_key=True)
        self.assertTrue(config.has_api_key)
        self.assertEqual(config.api_key, "abc")

    def test_repr_does_not_leak_key(self):
        config = NexusConfig(api_key="VERY_PRIVATE_TOKEN")
        self.assertNotIn("VERY_PRIVATE_TOKEN", repr(config))
        self.assertNotIn("api_key=", repr(config))
        self.assertNotIn("VERY_PRIVATE_TOKEN", str(config))

    def test_public_summary_never_leaks_credentials(self):
        config = NexusConfig(api_key="VERY_PRIVATE_TOKEN")
        printed = json.dumps(config.public_summary())
        self.assertTrue(config.public_summary()["api_key_configured"])
        self.assertNotIn("VERY_PRIVATE_TOKEN", printed)
        self.assertNotIn('"api_key"', printed)
        self.assertFalse(config.public_summary()["auto_execute_tools"])

    def test_rejects_non_nebius_base_url_to_protect_token(self):
        for url in ("http://api.tokenfactory.nebius.com/v1", "https://example.org/v1",
                    "https://api.tokenfactory.nebius.com/v2",
                    "https://api.tokenfactory.nebius.com/v1?token=abc",
                    "https://api.tokenfactory.nebius.com:443/v1"):
            with self.subTest(url=url), self.assertRaises(NexusConfigError):
                NexusConfig.from_env({"NEBIUS_BASE_URL": url})

    def test_official_base_url_with_trailing_slash(self):
        config = NexusConfig.from_env({"NEBIUS_BASE_URL": DEFAULT_BASE_URL + "/"})
        self.assertEqual(config.base_url, DEFAULT_BASE_URL)

    def test_rejects_bad_model(self):
        for value in ("", "../bad", "bad name", "x\nfoo", "x" * 202):
            with self.subTest(value=value[:20]), self.assertRaises(NexusConfigError):
                NexusConfig.from_env({"NEBIUS_MODEL": value})

    def test_custom_model_id(self):
        value = "nvidia/Test-Nemotron-1.0"
        self.assertEqual(NexusConfig.from_env({"NEBIUS_MODEL": value}).model, value)

    def test_validate_timeout(self):
        for value in ("0", "-1", "nan", "inf", "121", "bad"):
            with self.subTest(value=value), self.assertRaises(NexusConfigError):
                NexusConfig.from_env({"NEBIUS_TIMEOUT_SECONDS": value})
        self.assertEqual(NexusConfig.from_env({"NEBIUS_TIMEOUT_SECONDS": "2.5"}).timeout_seconds, 2.5)

    def test_validate_max_steps(self):
        for value in ("1", "13", "-1", "nan", "10000000000", "six"):
            with self.subTest(value=value), self.assertRaises(NexusConfigError):
                NexusConfig.from_env({"NEXUS_PLANNER_MAX_STEPS": value})
        self.assertEqual(NexusConfig.from_env({"NEXUS_PLANNER_MAX_STEPS": "12"}).planner_max_steps, 12)

    def test_validate_planner_tokens(self):
        for value in ("127", "8193", "", "3.5", "-100"):
            with self.subTest(value=value), self.assertRaises(NexusConfigError):
                NexusConfig.from_env({"NEXUS_PLANNER_MAX_TOKENS": value})
        self.assertEqual(NexusConfig.from_env({"NEXUS_PLANNER_MAX_TOKENS": "4096"}).planner_max_tokens, 4096)

    def test_rejects_non_string_env_value_and_invalid_mapping(self):
        with self.assertRaises(NexusConfigError):
            NexusConfig.from_env({"NEBIUS_MODEL": 42})
        with self.assertRaises(NexusConfigError):
            NexusConfig.from_env([])

    def test_input_mapping_not_modified(self):
        environ = {"NEBIUS_API_KEY": "abc", "NEXUS_PLANNER_MAX_STEPS": "4"}
        original = environ.copy()
        config = NexusConfig.from_env(environ)
        self.assertEqual(environ, original)
        self.assertEqual(config.planner_max_steps, 4)

    def test_credentials_reject_newline_and_control_characters(self):
        for value in ("abc\ndef", "abc\x00def"):
            with self.assertRaises(NexusConfigError):
                NexusConfig(api_key=value)

    def test_config_is_immutable(self):
        config = NexusConfig.from_env({})
        with self.assertRaises(FrozenInstanceError):
            config.model = "modified"

    def test_build_client_does_not_send_http_or_make_model_request(self):
        with patch.object(requests.sessions.Session, "request", side_effect=AssertionError("network call")):
            config = NexusConfig.from_env({"NEBIUS_API_KEY": "synthetic_test_token"})
            client = config.build_client()
            self.assertIsInstance(client, NebiusClient)
            self.assertEqual(client.model, DEFAULT_MODEL)
            client.close()

    def test_build_planner_from_fake_client(self):
        config = NexusConfig.from_env({"NEXUS_PLANNER_MAX_STEPS": "4",
                                       "NEXUS_PLANNER_MAX_TOKENS": "1024"})
        client = FakeClient()
        planner = config.build_planner(client=client)
        self.assertIsInstance(planner, NexusPlanner)
        self.assertIs(planner.client, client)
        self.assertEqual(planner.max_steps, 4)
        self.assertEqual(planner.max_tokens, 1024)

    def test_build_orchestrator_does_not_invoke_tools_or_model(self):
        executor = NexusExecutor()
        nexus = NexusConfig().build_orchestrator(executor, client=FakeClient())
        self.assertIsInstance(nexus, NexusOrchestrator)
        self.assertIs(nexus.executor, executor)

    def test_alias_points_to_same_config(self):
        self.assertIs(NexusSettings, NexusConfig)

    def test_rejects_invalid_ctor_types(self):
        with self.assertRaises(NexusConfigError):
            NexusConfig(planner_max_steps=True)
        with self.assertRaises(NexusConfigError):
            NexusConfig(timeout_seconds=math.inf)
        with self.assertRaises(NexusConfigError):
            NexusConfig(planner_max_tokens=True)


if __name__ == "__main__":
    unittest.main()
