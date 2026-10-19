"""Local CHIA 2.0.0 adapter: final CLI status beats recovered stderr warnings.

No installed package or third-party source is edited. All original diagnostics
stay in the result/logs; genuine failures still raise typed CHIA errors.
"""
from copy import copy
from datetime import datetime, timedelta, timezone
import json
import math
import re
import time

from chia.models.antigravity import AntigravityLLM, InvalidRequestError, RateLimitError


def provider_retry_seconds(message):
    match = re.search(r'(?:retry[-_ ]?after|retry[-_ ]?delay|retry\s+in|resets?\s+in)["\s:=]*(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|seconds?|m|minutes?|h|hours?|d|days?)?\b', message, re.I)
    if not match:
        return None
    number, unit = float(match[1]), (match[2] or "s").lower()
    return number * (0.001 if unit.startswith("ms") or unit.startswith("milli") else 60 if unit.startswith("m") else 3600 if unit.startswith("h") else 86400 if unit.startswith("d") else 1)


class ScaleAntigravityLLM(AntigravityLLM):
    def _classify_error(self, cli):
        final = next((event.get("result") for event in reversed(cli.events)
                      if event.get("event") == "result" and isinstance(event.get("result"), dict)), None)
        if (final is not None and final.get("status") == "SUCCESS"
                and cli.returncode == 0 and not final.get("error")
                and isinstance(final.get("response"), str) and final["response"].strip()
                and cli.result.strip() == final["response"].strip()):
            # The CLI may have retried an earlier 429 itself. Do NOT reject its
            # authoritative success because that old warning remains on stderr.
            return
        failure = copy(cli)
        if final is not None and (final.get("status") != "SUCCESS" or final.get("error")):
            failure.returncode = cli.returncode or 1
            if final.get("error"):
                # A terminal auth/billing error must not be hidden by an older
                # transient 429 warning in stderr.
                error = final["error"]
                failure.diagnostics = error if isinstance(error, str) else json.dumps(error)
        try:
            super()._classify_error(failure)
        except RateLimitError as error:
            seconds = provider_retry_seconds(failure.diagnostics)
            if seconds is not None:
                error.reset_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            raise
        # Exit code 0 or legacy/plain stdout alone is not proof of success.
        raise InvalidRequestError(self._get_node_id(), cli.returncode,
                                  "Missing, empty, or inconsistent explicit SUCCESS result from agy")


def retry_delay(error, attempt, settings):
    message = error.raw_message.lower()
    if any(text in message for text in ("daily quota", "daily limit", "weekly quota", "weekly limit", "billing", "payment", "insufficient credits")):
        return None
    reset = getattr(error, "reset_time", None)
    provider_wait = max(0, (reset - datetime.now(timezone.utc)).total_seconds()) if reset else 0
    seconds = math.ceil(max(settings["model_backoff_seconds"] * 2 ** attempt, provider_wait))
    # Never shorten a provider delay to fit our budget: stop instead.
    return seconds if seconds <= settings["model_max_backoff_seconds"] else None


def with_backoff(call, settings, on_retry, sleep=None):
    sleep = sleep or time.sleep
    for attempt in range(settings["model_rate_limit_retries"] + 1):
        try:
            return call()
        except RateLimitError as error:
            if attempt == settings["model_rate_limit_retries"]:
                raise
            delay = retry_delay(error, attempt, settings)
            if delay is None:
                raise
            on_retry(attempt + 1, delay, error)
            sleep(delay)
