"""Ollama 연결: 대화 모델(라우터·조회 에이전트용)과 법원 문서 추출기.

Ollama는 이 PC나 법인 서버에서 모델을 돌린다. 문서가 밖으로 나가지 않고 사용량 한도가 없다.
Gemini와 같은 인터페이스(ChatModel, Extractor)를 구현하므로 설정(LLM_PROVIDER)만 바꾸면 된다.

- PDF를 직접 받지 못하므로 텍스트 PDF는 텍스트를 뽑아 보내고, 스캔본은 쪽을 이미지로 바꿔 보낸다.
- 생각(thinking) 기능은 끈다. 이 용도에서는 느려지기만 한다.
"""

from __future__ import annotations

import base64
import io
import json
from collections.abc import Iterator
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from lawca.agent.llm import Chunk, TextDelta, ToolCall, ToolSpec, Turn, TurnEnd
from lawca.extraction.gemini import PROMPT as EXTRACTION_PROMPT
from lawca.extraction.gemini import ExtractionError, ModelUnavailableError
from lawca.extraction.schema import CourtDocument
from lawca.extraction.validate import has_text, pdf_text_pages

T = TypeVar("T", bound=BaseModel)

# CPU에서는 답이 한참 걸리므로 읽기 제한 시간을 넉넉히 둔다.
TIMEOUT = httpx.Timeout(10.0, read=900.0)


class OllamaClient:
    def __init__(self, base_url: str, model: str, num_gpu: int | None = None, transport: httpx.BaseTransport | None = None):
        self.model = model
        self._num_gpu = num_gpu
        self._http = httpx.Client(base_url=base_url, timeout=TIMEOUT, transport=transport)

    def _body(self, messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": 0}
        if self._num_gpu is not None:
            options["num_gpu"] = self._num_gpu
        return {"model": self.model, "messages": messages, "think": False, "options": options, **extra}

    def _fail(self, response: httpx.Response) -> None:
        detail = response.text[:300]
        try:
            detail = response.json().get("error", detail)
        except ValueError:
            pass
        if response.status_code == 404:
            raise ExtractionError(f"Ollama에 {self.model} 모델이 없습니다. `ollama pull {self.model}`로 받으세요.")
        if response.status_code >= 500:
            raise ModelUnavailableError(f"Ollama({self.model})가 응답하지 못했습니다: {detail}")
        raise ExtractionError(f"Ollama({self.model}) 요청이 거부되었습니다({response.status_code}): {detail}")

    def chat(self, messages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        try:
            response = self._http.post("/api/chat", json=self._body(messages, stream=False, **extra))
        except httpx.TransportError as exc:
            raise ModelUnavailableError(f"Ollama 서버에 연결하지 못했습니다({exc}). Ollama가 실행 중인지 확인하세요.") from exc
        if response.status_code != 200:
            self._fail(response)
        return response.json()["message"]

    def chat_stream(self, messages: list[dict[str, Any]], **extra: Any) -> Iterator[dict[str, Any]]:
        try:
            with self._http.stream("POST", "/api/chat", json=self._body(messages, stream=True, **extra)) as response:
                if response.status_code != 200:
                    response.read()
                    self._fail(response)
                for line in response.iter_lines():
                    if line.strip():
                        yield json.loads(line)
        except httpx.TransportError as exc:
            raise ModelUnavailableError(f"Ollama 서버에 연결하지 못했습니다({exc}). Ollama가 실행 중인지 확인하세요.") from exc


def _message(turn: Turn) -> list[dict[str, Any]]:
    if turn.raw is not None:
        return [turn.raw]
    if turn.role == "tool":
        return [
            {"role": "tool", "tool_name": name, "content": json.dumps(result, ensure_ascii=False)}
            for name, result in turn.tool_results
        ]
    return [{"role": "assistant" if turn.role == "model" else "user", "content": turn.text}]


def _messages(system: str, turns: list[Turn]) -> list[dict[str, Any]]:
    return [{"role": "system", "content": system}, *(m for t in turns for m in _message(t))]


class OllamaChat:
    """라우터·조회 에이전트용 대화 모델(ChatModel)."""

    def __init__(self, client: OllamaClient) -> None:
        self._client = client
        self.model = client.model

    def stream(self, system: str, turns: list[Turn], tools: list[ToolSpec]) -> Iterator[Chunk]:
        specs = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in tools
        ]
        text = ""
        calls: list[dict[str, Any]] = []
        for part in self._client.chat_stream(_messages(system, turns), tools=specs or None):
            message = part.get("message") or {}
            if message.get("content"):
                text += message["content"]
                yield TextDelta(message["content"])
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    arguments = json.loads(arguments or "{}")
                calls.append(call)
                yield ToolCall(function.get("name", ""), dict(arguments))
        raw: dict[str, Any] = {"role": "assistant", "content": text}
        if calls:
            raw["tool_calls"] = calls
        yield TurnEnd(raw)

    def structured(self, system: str, turns: list[Turn], schema: type[T]) -> T:
        message = self._client.chat(_messages(system, turns), format=schema.model_json_schema())
        try:
            return schema.model_validate_json(message.get("content") or "")
        except ValueError as exc:
            raise ExtractionError(f"{self.model} 응답을 해석하지 못했습니다: {exc}") from exc


def render_pages(pdf: bytes, max_pages: int) -> list[str]:
    """PDF 앞쪽 몇 쪽을 PNG(base64)로 바꾼다. 스캔본을 비전 모델에 보낼 때 쓴다."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    images = []
    for index in range(min(len(document), max_pages)):
        bitmap = document[index].render(scale=150 / 72)  # 150dpi: 글자를 읽을 만하면서 너무 크지 않게
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format="PNG")
        images.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
    return images


class OllamaExtractor:
    """법원 문서 추출기(Extractor)."""

    def __init__(self, client: OllamaClient, max_image_pages: int = 3) -> None:
        self._client = client
        self._max_image_pages = max_image_pages
        self.model = client.model

    def extract(self, pdf: bytes) -> CourtDocument:
        pages = pdf_text_pages(pdf)
        if has_text(pages):
            text = "\n\n".join(f"[{i}쪽]\n{page}" for i, page in enumerate(pages, start=1))
            message: dict[str, Any] = {
                "role": "user",
                "content": f"{EXTRACTION_PROMPT}\n아래는 PDF에서 뽑은 텍스트입니다. [n쪽] 표시가 쪽 번호입니다.\n\n{text}",
            }
        else:
            images = render_pages(pdf, self._max_image_pages)
            message = {
                "role": "user",
                "content": f"{EXTRACTION_PROMPT}\n첨부 이미지는 PDF의 1쪽부터 차례로 {len(images)}쪽입니다.",
                "images": images,
            }
        reply = self._client.chat([message], format=CourtDocument.model_json_schema())
        try:
            return CourtDocument.model_validate_json(reply.get("content") or "")
        except ValueError as exc:
            raise ExtractionError(f"{self.model} 추출 결과를 해석하지 못했습니다: {exc}") from exc
