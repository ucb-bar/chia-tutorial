"""SkyDiscover adapters for the head-side search controller."""

import asyncio
import json
from chia.base.ChiaFunction import get, chia_cancel
from skydiscover.llm.base import LLMInterface, LLMResponse
from .agent import CONSTRAINTS, generate


class CLIClient(LLMInterface):
    def __init__(self, configuration, experiment):
        self.configuration, self.experiment = configuration, experiment

    async def generate(self, system_message, messages, **kwargs):
        user_message = messages[-1]
        selection = json.loads(user_message["content"])
        candidate_id = selection["request_id"]
        request = self.experiment.selection(candidate_id)
        handle = generate.chia_remote(
            self.configuration,
            self.experiment.sql,
            request,
            _chia_tag="proposal/" + candidate_id,
        )
        try:
            saved = await asyncio.to_thread(get, handle)
        except asyncio.CancelledError:
            chia_cancel(getattr(handle, "ref", handle), force=True)
            raise
        except Exception:
            from .profiling import record_scores
            record_scores(self.experiment, candidate_id, {"validity": 0}, stage="generation")
            raise
        candidate = {"proposal_id": candidate_id, "patch": saved["patch"]}
        return LLMResponse(text="```json\n" + json.dumps(candidate) + "\n```")


class ParentContext:
    def build_prompt(self, current_program, context):
        return {
            "system": CONSTRAINTS,
            "user": json.dumps({"request_id": current_program.metadata["request_id"]}),
        }
