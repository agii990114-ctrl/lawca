"""테스트용 가짜 대화 모델. 정해진 분류 결과와 대본(청크 목록)대로 답한다."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypeVar

from pydantic import BaseModel

from lawca.agent.llm import Chunk, ToolSpec, Turn, TurnEnd
from lawca.agent.draft import FieldValue, FormRequest
from lawca.agent.router import RouteDecision, RoutedTask
from lawca.extraction.gemini import ModelUnavailableError

T = TypeVar("T", bound=BaseModel)


class FakeChatModel:
    def __init__(
        self,
        model: str = "fake",
        tasks: list[tuple[str, str]] | None = None,
        script: list[list[Chunk]] | None = None,
        unavailable: bool = False,
        form_request: tuple[str | None, str | None, dict[str, str]] | None = None,
        brief: BaseModel | None = None,
        draft: BaseModel | None = None,
    ) -> None:
        self.model = model
        self.tasks = tasks or [("help", "")]
        self.script = list(script or [])
        self.unavailable = unavailable
        self.form_request = form_request or (None, None, {})
        self.brief = brief
        self.draft = draft
        self.seen_turns: list[list[Turn]] = []

    def structured(self, system: str, turns: list[Turn], schema: type[T]) -> T:
        if self.unavailable:
            raise ModelUnavailableError(f"{self.model} 혼잡")
        self.seen_turns.append(list(turns))
        if schema is FormRequest:
            form_id, case_number, values = self.form_request
            items = [FieldValue(key=k, value=v) for k, v in values.items()]
            return FormRequest(form_id=form_id, case_number=case_number, values=items)  # type: ignore[return-value]
        if schema.__name__ == "BriefDraft":
            assert self.draft is not None, "본문 초안 대본이 없습니다"
            return self.draft  # type: ignore[return-value]
        if schema.__name__ == "BriefSummary":
            assert self.brief is not None, "brief 요약 대본이 없습니다"
            return self.brief  # type: ignore[return-value]
        assert schema is RouteDecision
        return RouteDecision(tasks=[RoutedTask(label=label, request=req) for label, req in self.tasks])  # type: ignore[return-value]

    def stream(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Iterator[Chunk]:
        if self.unavailable:
            raise ModelUnavailableError(f"{self.model} 혼잡")
        self.seen_turns.append(list(turns))
        chunks = self.script.pop(0) if self.script else []
        yield from chunks
        yield TurnEnd(None)


class FakeEmbedder:
    """글자 두 개씩(바이그램)을 1024칸에 흩어 담는 가짜 임베딩. 겹치는 글자가 많을수록 가깝다."""

    model = "fake-embed"

    def __init__(self, unavailable: bool = False) -> None:
        self.unavailable = unavailable
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        from lawca.library import EMBED_DIM, EmbeddingUnavailable

        if self.unavailable:
            raise EmbeddingUnavailable("가짜 임베딩 꺼짐")
        self.calls += 1
        out = []
        for text in texts:
            vector = [0.0] * EMBED_DIM
            compact = "".join(text.split())
            for a, b in zip(compact, compact[1:]):
                vector[hash(a + b) % EMBED_DIM] += 1.0
            norm = sum(x * x for x in vector) ** 0.5 or 1.0
            out.append([x / norm for x in vector])
        return out
