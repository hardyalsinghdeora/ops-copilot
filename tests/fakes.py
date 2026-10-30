"""A scripted chat model so the whole graph runs with no API key.

Each call to ``invoke`` pops the next scripted ``AIMessage``; ``with_structured_output``
pops the next scripted pydantic object. Tests therefore assert on *graph behaviour*
(routing, tool execution, evidence capture, review loop, interrupts) rather than on
model quality — which is exactly what unit tests should pin down.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda


class ScriptedChatModel(BaseChatModel):
    """``responses``: AIMessages returned in order by invoke(); ``structured``: objects
    returned in order by with_structured_output(...).invoke()."""

    responses: list[AIMessage] = []
    structured: list[Any] = []
    calls: list[list[BaseMessage]] = []

    def model_post_init(self, __context: Any) -> None:
        self._responses = deque(self.responses)
        self._structured = deque(self.structured)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls.append(messages)
        if not self._responses:
            raise AssertionError("ScriptedChatModel ran out of scripted responses")
        return ChatResult(generations=[ChatGeneration(message=self._responses.popleft())])

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        def _next(_input):
            if not self._structured:
                raise AssertionError("ScriptedChatModel ran out of scripted structured outputs")
            return self._structured.popleft()

        return RunnableLambda(_next)
