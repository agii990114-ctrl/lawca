"""Ollama 연결 테스트. 실제 Ollama 서버 없이 가짜 HTTP 응답(httpx.MockTransport)으로 확인한다."""

import json
from pathlib import Path

import httpx
import pytest

from lawca.agent.llm import TextDelta, ToolCall, ToolSpec, Turn, TurnEnd
from lawca.agent.router import RouteDecision
from lawca.extraction.gemini import ExtractionError, ModelUnavailableError
from lawca.ollama import OllamaChat, OllamaClient, OllamaExtractor
from tests.test_extraction import correction_order

SAMPLE_PDF = Path(__file__).parent / "fixtures" / "synthetic_correction_order.pdf"
TOOLS = [ToolSpec("list_deadlines", "기한 조회", {"type": "object", "properties": {"range": {"type": "string"}}})]


def client_with(handler, num_gpu=None) -> tuple[OllamaClient, list[dict]]:
    """handler(요청 본문) → httpx.Response. 보낸 요청 본문을 모아 돌려준다."""
    sent: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        return handler(body)

    return OllamaClient("http://ollama.test", "gemma-test", num_gpu, transport=httpx.MockTransport(respond)), sent


def ndjson(*parts: dict) -> httpx.Response:
    return httpx.Response(200, content="\n".join(json.dumps(p, ensure_ascii=False) for p in parts).encode())


def test_stream_yields_tool_call_then_text_and_keeps_raw_message():
    replies = iter(
        [
            ndjson(
                {"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "list_deadlines", "arguments": {"range": "this_week"}}}]}, "done": False},
                {"message": {"role": "assistant", "content": ""}, "done": True},
            ),
            ndjson(
                {"message": {"role": "assistant", "content": "이번 주 "}, "done": False},
                {"message": {"role": "assistant", "content": "기한은 1건입니다."}, "done": True},
            ),
        ]
    )
    client, sent = client_with(lambda body: next(replies), num_gpu=0)
    chat = OllamaChat(client)

    first = list(chat.stream("시스템", [Turn("user", "이번 주 기한")], TOOLS))
    assert first[0] == ToolCall("list_deadlines", {"range": "this_week"})
    assert isinstance(first[-1], TurnEnd) and first[-1].raw["tool_calls"]

    turns = [Turn("user", "이번 주 기한"), Turn("model", raw=first[-1].raw), Turn("tool", tool_results=[("list_deadlines", {"count": 1})])]
    second = list(chat.stream("시스템", turns, TOOLS))
    assert [c.text for c in second if isinstance(c, TextDelta)] == ["이번 주 ", "기한은 1건입니다."]

    # 요청 형식: 시스템 메시지, 도구 정의, 생각 끄기, CPU 설정, 도구 결과 메시지
    assert sent[0]["messages"][0] == {"role": "system", "content": "시스템"}
    assert sent[0]["tools"][0]["function"]["name"] == "list_deadlines"
    assert sent[0]["think"] is False and sent[0]["options"]["num_gpu"] == 0
    assert sent[1]["messages"][-1] == {"role": "tool", "tool_name": "list_deadlines", "content": '{"count": 1}'}


def test_structured_uses_json_schema_format():
    client, sent = client_with(
        lambda body: httpx.Response(200, json={"message": {"role": "assistant", "content": '{"tasks": [{"label": "query", "request": "기한"}]}'}})
    )
    decision = OllamaChat(client).structured("분류", [Turn("user", "기한 알려줘")], RouteDecision)
    assert decision.tasks[0].label == "query"
    assert sent[0]["format"]["properties"]["tasks"]
    assert sent[0]["stream"] is False


def test_unparseable_structured_reply_is_an_error():
    client, _ = client_with(lambda body: httpx.Response(200, json={"message": {"content": "모르겠어요"}}))
    with pytest.raises(ExtractionError):
        OllamaChat(client).structured("분류", [Turn("user", "?")], RouteDecision)


def test_missing_model_explains_how_to_pull():
    client, _ = client_with(lambda body: httpx.Response(404, json={"error": "model not found"}))
    with pytest.raises(ExtractionError, match="ollama pull gemma-test"):
        OllamaChat(client).structured("분류", [Turn("user", "?")], RouteDecision)


def test_server_error_and_connection_failure_are_unavailable():
    client, _ = client_with(lambda body: httpx.Response(500, json={"error": "CUDA error"}))
    with pytest.raises(ModelUnavailableError, match="CUDA error"):
        list(OllamaChat(client).stream("s", [Turn("user", "hi")], []))

    def refuse(request):
        raise httpx.ConnectError("connection refused")

    down = OllamaClient("http://ollama.test", "gemma-test", transport=httpx.MockTransport(refuse))
    with pytest.raises(ModelUnavailableError, match="연결하지 못했습니다"):
        OllamaChat(down).structured("s", [Turn("user", "hi")], RouteDecision)


def test_extractor_sends_pdf_text_with_page_marks():
    expected = correction_order()
    client, sent = client_with(lambda body: httpx.Response(200, json={"message": {"content": expected.model_dump_json()}}))
    doc = OllamaExtractor(client).extract(SAMPLE_PDF.read_bytes())
    assert doc.case_number.value == "2026가단51234"
    content = sent[0]["messages"][0]["content"]
    assert "[1쪽]" in content and "2026가단51234" in content
    assert "images" not in sent[0]["messages"][0]
    assert sent[0]["format"]["properties"]["document_type"]


def test_extractor_sends_images_for_scanned_pdf(monkeypatch):
    import lawca.ollama as module

    monkeypatch.setattr(module, "pdf_text_pages", lambda pdf: [""])  # 텍스트 없는 스캔본처럼
    expected = correction_order()
    client, sent = client_with(lambda body: httpx.Response(200, json={"message": {"content": expected.model_dump_json()}}))
    OllamaExtractor(client, max_image_pages=2).extract(SAMPLE_PDF.read_bytes())
    images = sent[0]["messages"][0]["images"]
    assert len(images) == 1  # 1쪽짜리 문서
    assert images[0].startswith("iVBOR")  # PNG(base64)
