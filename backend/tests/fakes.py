"""테스트용 가짜 대화 모델. 정해진 분류 결과와 대본(청크 목록)대로 답한다."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TypeVar

from pydantic import BaseModel

from lawca.agent.llm import Chunk, ToolSpec, Turn, TurnEnd
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
    ) -> None:
        self.model = model
        self.tasks = tasks or [("help", "")]
        self.script = list(script or [])
        self.unavailable = unavailable
        self.seen_turns: list[list[Turn]] = []

    def structured(self, system: str, turns: list[Turn], schema: type[T]) -> T:
        if self.unavailable:
            raise ModelUnavailableError(f"{self.model} 혼잡")
        self.seen_turns.append(list(turns))
        assert schema is RouteDecision
        return RouteDecision(tasks=[RoutedTask(label=label, request=req) for label, req in self.tasks])  # type: ignore[return-value]

    def stream(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Iterator[Chunk]:
        if self.unavailable:
            raise ModelUnavailableError(f"{self.model} 혼잡")
        self.seen_turns.append(list(turns))
        chunks = self.script.pop(0) if self.script else []
        yield from chunks
        yield TurnEnd(None)
