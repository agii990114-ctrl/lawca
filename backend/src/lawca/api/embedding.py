"""자료실 임베딩 모델 의존성. 테스트는 가짜로 바꾼다."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends

from lawca.config import Settings, get_settings
from lawca.library import Embedder, OllamaEmbedder


def get_embedder_factory(settings: Annotated[Settings, Depends(get_settings)]) -> Callable[[], Embedder | None]:
    """임베딩 모델을 만드는 함수. 꺼져 있으면 None(키워드 검색만)."""

    def make() -> Embedder | None:
        if not settings.embeddings_enabled:
            return None
        return OllamaEmbedder(settings.ollama_url, settings.embedding_model, settings.ollama_num_gpu)

    return make


EmbedderFactory = Annotated[Callable[[], Embedder | None], Depends(get_embedder_factory)]
