"""No network/model use: replay recovered warnings and terminal failures."""
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from chia.models.antigravity import AntigravityLLM, AntigravityQueryResult, AuthenticationError, BillingError, InvalidRequestError, RateLimitError
from model_client import ScaleAntigravityLLM, provider_retry_seconds, with_backoff


class ModelClientTests(unittest.TestCase):
    def setUp(self):
        self.llm = object.__new__(ScaleAntigravityLLM)
        self.llm.logger = logging.getLogger("scale-model-test")
        self.llm._get_node_id = lambda: "test-node"
        self.settings = dict(model_rate_limit_retries=2, model_backoff_seconds=30, model_max_backoff_seconds=120)

    def result(self, status="SUCCESS", response="real answer", warning="", error=None, code=0):
        final = dict(status=status, response=response, conversation_id="test-session")
        if error is not None:
            final["error"] = error
        return AntigravityQueryResult(result=response, returncode=code, stderr=warning, stream_result="",
            diagnostics=warning, events=[dict(event="result", result=final)])

    def test_authoritative_success_accepts_recovered_429_without_erasing_diagnostics(self):
        cli = self.result(warning="API error: RESOURCE_EXHAUSTED (429), retrying in 4s")
        # Reproduce the CHIA 2.0.0 false positive, then verify the local adapter.
        with self.assertRaises(RateLimitError): AntigravityLLM._classify_error(self.llm, cli)
        self.llm._classify_error(cli)
        self.assertIn("429", cli.stderr)
        self.assertIn("429", cli.diagnostics)
        self.llm._classify_error(self.result())

    def test_missing_empty_failed_or_inconsistent_results_are_not_success(self):
        cases = [self.result(response=""), self.result(status="FAILED"), self.result(code=1)]
        missing = self.result(); missing.events = []
        inconsistent = self.result(); inconsistent.result = "different answer"
        for cli in cases + [missing, inconsistent]:
            with self.subTest(cli=cli), self.assertRaises(Exception): self.llm._classify_error(cli)

    def test_terminal_failure_takes_precedence_over_an_old_429_warning(self):
        for error, cls in [("Unauthorized 401", AuthenticationError), ("Billing payment required 402", BillingError)]:
            cli = self.result(status="FAILED", warning="earlier 429 retry", error=error)
            with self.assertRaises(cls): self.llm._classify_error(cli)

    def test_true_429_is_retried_twice_with_bounded_backoff(self):
        error = RateLimitError("test", reset_time=datetime.now(timezone.utc), raw_message="429 temporary capacity")
        call = Mock(side_effect=[error, error, "answer"])
        sleep, log = Mock(), Mock()
        self.assertEqual(with_backoff(call, self.settings, log, sleep), "answer")
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [30, 60])
        self.assertEqual(call.call_count, 3)
        call = Mock(side_effect=error)
        with self.assertRaises(RateLimitError): with_backoff(call, self.settings, log, sleep)
        self.assertEqual(call.call_count, 3)

    def test_provider_delay_is_respected_or_stops_without_an_early_retry(self):
        cli = self.result(status="FAILED", error='RESOURCE_EXHAUSTED 429; retryDelay: "90s"')
        with self.assertRaises(RateLimitError) as raised: self.llm._classify_error(cli)
        sleep, log = Mock(), Mock()
        call = Mock(side_effect=[raised.exception, "answer"])
        self.assertEqual(with_backoff(call, self.settings, log, sleep), "answer")
        self.assertGreaterEqual(sleep.call_args.args[0], 90)
        for message, wait in [("weekly quota exhausted", 0), ("daily limit", 0), ("429 capacity", 180)]:
            error = RateLimitError("test", reset_time=datetime.now(timezone.utc) + timedelta(seconds=wait), raw_message=message)
            call, sleep = Mock(side_effect=error), Mock()
            with self.assertRaises(RateLimitError): with_backoff(call, self.settings, log, sleep)
            self.assertEqual(call.call_count, 1)
            sleep.assert_not_called()

    def test_auth_and_billing_failures_are_not_retried(self):
        for cls in (AuthenticationError, BillingError, InvalidRequestError):
            call, sleep = Mock(side_effect=cls("test", 1, "terminal")), Mock()
            with self.assertRaises(cls): with_backoff(call, self.settings, Mock(), sleep)
            self.assertEqual(call.call_count, 1)
            sleep.assert_not_called()

    def test_retry_delay_formats(self):
        for message, expected in [('Retry-After: 7', 7), ('retryDelay: "1.5s"', 1.5), ('Resets in 0s.', 0), ('Retry in 250ms', .25), ('Resets in 2 minutes', 120), ('resets in 3 hours', 10800), ('resets in 7 days', 604800)]:
            self.assertEqual(provider_retry_seconds(message), expected)
        self.assertIsNone(provider_retry_seconds("429 without a delay"))


if __name__ == "__main__":
    unittest.main()
