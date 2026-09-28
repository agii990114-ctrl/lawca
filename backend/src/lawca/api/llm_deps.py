"""대화 모델·문서 추출기 의존성. 테스트는 가짜로 바꾼다(app.dependency_overrides)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException

from lawca.agent.graph import ModelsNotConfigured
from lawca.agent.llm import ChatModel, gemini_models
from lawca.config import Settings, get_settings
from lawca.extraction.gemini import Extractor, GeminiExtractor
from lawca.lawapi import LawApi, LawApiClient
from lawca.ollama import OllamaChat, OllamaClient, OllamaExtractor


def _ollama(settings: Settings) -> OllamaClient:
    return OllamaClient(settings.ollama_url, settings.ollama_model, settings.ollama_num_gpu)


def get_extractor(settings: Annotated[Settings, Depends(get_settings)]) -> Extractor:
    if settings.llm_provider == "ollama":
        return OllamaExtractor(_ollama(settings), settings.ollama_max_image_pages)
    if not settings.gemini_api_key:
        raise HTTPException(503, "Gemini API 키가 설정되지 않았습니다. 레포 루트 .env에 GEMINI_API를 넣으세요.")
    return GeminiExtractor(settings.gemini_api_key, settings.gemini_extraction_models)


def get_extractor_factory(settings: Annotated[Settings, Depends(get_settings)]) -> Callable[[], Extractor]:
    """추출기를 만드는 함수를 넘긴다. 글만 보낸 요청은 키가 없어도 응답하도록 필요할 때 만든다."""
    return lambda: get_extractor(settings)


def get_models_factory(settings: Annotated[Settings, Depends(get_settings)]) -> Callable[[], list[ChatModel]]:
    """라우터·조회 에이전트가 쓸 대화 모델 목록(기본 모델 먼저, 그다음 예비 모델)을 만드는 함수."""

    def make() -> list[ChatModel]:
        if settings.llm_provider == "ollama":
            return [OllamaChat(_ollama(settings))]
        if not settings.gemini_api_key:
            raise ModelsNotConfigured("Gemini API 키가 설정되지 않았습니다. 레포 루트 .env에 GEMINI_API를 넣으세요.")
        return gemini_models(settings.gemini_api_key, settings.gemini_models)

    return make


ModelsFactory = Annotated[Callable[[], list[ChatModel]], Depends(get_models_factory)]


_law_clients: dict[str, LawApiClient] = {}


def get_law_api(settings: Annotated[Settings, Depends(get_settings)]) -> LawApi:
    """국가법령정보센터 클라이언트. 같은 키면 하나를 계속 써서 캐시를 살린다."""
    if not settings.law_api_key:
        raise HTTPException(503, "국가법령정보센터 키가 없습니다. 레포 루트 .env에 LAW_API를 넣으세요.")
    client = _law_clients.get(settings.law_api_key)
    if client is None:
        client = _law_clients[settings.law_api_key] = LawApiClient(settings.law_api_key)
    return client


LawApiDep = Annotated[LawApi, Depends(get_law_api)]
