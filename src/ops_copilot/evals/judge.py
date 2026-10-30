"""LLM-as-judge for generated answers (RAGAS-style, simplified).

* **faithfulness** — are the answer's claims supported by the retrieved context?
* **answer relevance** — does the answer address the question?
* **reference agreement** — does it agree with the human-written reference answer?

Scores are 1–5 with a one-line rationale, produced as structured output by the
``fast`` model so a full run over the golden set costs cents, not dollars.
"""

from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

JUDGE_SYSTEM = """You are an impartial grader for a retrieval-augmented assistant.
Score strictly. A claim is only "supported" if the CONTEXT states it; general knowledge does not count.
Fabricated identifiers, commands or numbers cap faithfulness at 2."""


class JudgeScores(BaseModel):
    faithfulness: int = Field(ge=1, le=5)
    relevance: int = Field(ge=1, le=5)
    reference_agreement: int = Field(ge=1, le=5)
    rationale: str


def judge_answer(llm: BaseChatModel, question: str, answer: str, context: str, reference: str) -> JudgeScores:
    grader = llm.with_structured_output(JudgeScores)
    prompt = (
        f"QUESTION:\n{question}\n\nANSWER:\n{answer}\n\nCONTEXT (retrieved):\n{context}\n\n"
        f"REFERENCE ANSWER (human-written):\n{reference}"
    )
    return grader.invoke([SystemMessage(content=JUDGE_SYSTEM), HumanMessage(content=prompt)])
