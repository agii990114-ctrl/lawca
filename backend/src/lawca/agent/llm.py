"""에이전트용 LLM 어댑터.

에이전트 코드는 이 모듈의 타입(Turn, ToolSpec, 청크)만 쓰고 Gemini 타입을 모른다. 테스트에서는 가짜 모델로 바꾼다.

Gemini 3 계열은 도구 호출에 서명(thought signature)을 붙여 보내고, 다음 요청에 그대로 돌려받아야 한다.
그래서 모델 응답은 원래 객체(Turn.raw)를 보관해 다시 보낸다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from lawca.extraction.gemini import ExtractionError, ModelUnavailableError

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class TurnEnd:
    """모델 응답 한 번이 끝났다. raw는 다음 요청에 그대로 돌려보낼 모델 응답이다."""

    raw: Any


Chunk = ToolCall | TextDelta | TurnEnd


@dataclass
class Turn:
    role: Literal["user", "model", "tool"]
    text: str = ""
    tool_results: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    raw: Any = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


class ChatModel(Protocol):
    model: str

    def stream(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Iterator[Chunk]: ...

    def structured(self, system: str, turns: list[Turn], schema: type[T]) -> T: ...


def _content(turn: Turn) -> types.Content:
    if turn.raw is not None:
        return turn.raw
    if turn.role == "tool":
        parts = [types.Part.from_function_response(name=name, response=result) for name, result in turn.tool_results]
        return types.Content(role="user", parts=parts)
    return types.Content(role=turn.role, parts=[types.Part(text=turn.text)])


class GeminiChat:
    """모델 하나에 묶인 대화 모델. 예비 모델 전환은 호출하는 쪽(with_fallback)이 한다."""

    def __init__(self, client: genai.Client, model: str) -> None:
        self._client = client
        self.model = model

    def _config(self, system: str, **extra: Any) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction=system,
            temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            **extra,
        )

    def stream(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Iterator[Chunk]:
        declarations = [
            types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
            for t in tools
        ]
        config = self._config(system, tools=[types.Tool(function_declarations=declarations)] if tools else None)
        parts: list[types.Part] = []
        try:
            for chunk in self._client.models.generate_content_stream(
                model=self.model, contents=[_content(t) for t in turns], config=config
            ):
                if not chunk.candidates or chunk.candidates[0].content is None:
                    continue
                for part in chunk.candidates[0].content.parts or []:
                    parts.append(part)
                    if part.function_call and part.function_call.name:
                        yield ToolCall(part.function_call.name, dict(part.function_call.args or {}))
                    elif part.text and not part.thought:
                        yield TextDelta(part.text)
        except errors.ServerError as exc:
            raise ModelUnavailableError(f"{self.model} 서버가 응답하지 않습니다({exc.code}).") from exc
        except errors.ClientError as exc:
            if exc.code == 429:
                raise ModelUnavailableError(f"{self.model} 사용량 한도를 넘었습니다(429).") from exc
            raise ExtractionError(f"{self.model} 요청이 거부되었습니다({exc.code}): {exc.message}") from exc
        yield TurnEnd(types.Content(role="model", parts=parts))

    def structured(self, system: str, turns: list[Turn], schema: type[T]) -> T:
        config = self._config(system, response_mime_type="application/json", response_schema=schema)
        try:
            response = self._client.models.generate_content(
                model=self.model, contents=[_content(t) for t in turns], config=config
            )
        except errors.ServerError as exc:
            raise ModelUnavailableError(f"{self.model} 서버가 응답하지 않습니다({exc.code}).") from exc
        except errors.ClientError as exc:
            if exc.code == 429:
                raise ModelUnavailableError(f"{self.model} 사용량 한도를 넘었습니다(429).") from exc
            raise ExtractionError(f"{self.model} 요청이 거부되었습니다({exc.code}): {exc.message}") from exc
        if isinstance(response.parsed, schema):
            return response.parsed
        try:
            return schema.model_validate_json(response.text or "")
        except ValueError as exc:
            raise ExtractionError(f"응답을 해석하지 못했습니다: {exc}") from exc


def gemini_models(api_key: str, models: list[str]) -> list[ChatModel]:
    client = genai.Client(api_key=api_key)
    return [GeminiChat(client, m) for m in models]


R = TypeVar("R")


def all_unavailable(errors_: list[ModelUnavailableError]) -> ModelUnavailableError:
    """모든 모델이 실패했을 때 모델별 사유를 모아 하나의 오류로 만든다."""
    if not errors_:
        return ModelUnavailableError("사용할 모델이 없습니다.")
    if len(errors_) == 1:
        return errors_[0]
    return ModelUnavailableError("모든 모델을 쓸 수 없습니다. " + " / ".join(str(e) for e in errors_))


def with_fallback(models: list[ChatModel], run: Callable[[ChatModel], R]) -> R:
    """앞 모델 서버가 혼잡하거나 한도에 걸리면 다음 모델로 처음부터 다시 실행한다."""
    failures: list[ModelUnavailableError] = []
    for model in models:
        try:
            return run(model)
        except ModelUnavailableError as exc:
            failures.append(exc)
    raise all_unavailable(failures)
