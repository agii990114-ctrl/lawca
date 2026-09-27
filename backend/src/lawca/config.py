"""설정. 레포 루트의 .env와 backend/.env를 읽는다."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 어떤 LLM을 쓸지. ollama는 이 PC(또는 법인 서버)에서 돌아서 문서가 밖으로 나가지 않고 사용량 한도가 없다.
    llm_provider: Literal["gemini", "ollama"] = Field(default="gemini", validation_alias="LLM_PROVIDER")
    ollama_url: str = Field(default="http://localhost:11434", validation_alias="OLLAMA_URL")
    ollama_model: str = Field(default="gemma4:e4b", validation_alias="OLLAMA_MODEL")
    # GPU에 올릴 층 수. 0이면 CPU만 쓴다(GPU 드라이버가 맞지 않을 때). 비우면 Ollama가 정한다.
    ollama_num_gpu: int | None = Field(default=None, validation_alias="OLLAMA_NUM_GPU")
    # 스캔본 PDF를 이미지로 보낼 때 최대 쪽수. CPU에서는 쪽마다 오래 걸린다.
    ollama_max_image_pages: int = Field(default=3, validation_alias="OLLAMA_MAX_IMAGE_PAGES")

    gemini_api_key: str | None = Field(default=None, validation_alias=AliasChoices("GEMINI_API", "GEMINI_API_KEY"))
    # 별칭(gemini-flash-latest 등)은 가리키는 모델이 바뀌어 평가를 재현할 수 없으므로 버전을 고정한다.
    # 기본 모델: 요청 분류, 서식 요청 해석, 조회 에이전트. 가볍고 싼 Lite를 쓴다.
    gemini_model: str = Field(default="gemini-3.5-flash-lite", validation_alias="GEMINI_MODEL")
    # 앞 모델이 혼잡(503)하거나 한도(429)에 걸릴 때만 차례로 쓰는 예비 모델(쉼표로 구분). 응답에는 실제로 쓴 모델을 기록한다.
    gemini_fallback_models: str = Field(
        default="gemini-3.1-flash-lite,gemini-3.7-flash", validation_alias="GEMINI_FALLBACK_MODELS"
    )
    # 문서 추출 모델. 기한 계산의 출발점이라 정확도가 가장 중요해 Flash를 따로 둔다.
    gemini_extraction_model: str = Field(default="gemini-3.7-flash", validation_alias="GEMINI_EXTRACTION_MODEL")
    # 마지막의 gemini-flash-latest는 별칭이라 최후의 수단으로만 둔다.
    gemini_extraction_fallback_models: str = Field(
        default="gemini-3.5-flash,gemini-flash-latest", validation_alias="GEMINI_EXTRACTION_FALLBACK_MODELS"
    )
    max_upload_mb: int = 20
    # docker-compose.yml의 개발용 PostgreSQL. 운영에서는 DATABASE_URL로 바꾼다.
    database_url: str = Field(
        default="postgresql+psycopg://lawca:lawca@localhost:5433/lawca", validation_alias="DATABASE_URL"
    )

    @staticmethod
    def _chain(first: str, rest: str) -> list[str]:
        models = [first, *(m.strip() for m in rest.split(","))]
        return list(dict.fromkeys(m for m in models if m))

    @property
    def gemini_models(self) -> list[str]:
        """요청 분류·서식 해석·조회에 시도할 모델 목록. 기본 모델이 먼저이고 중복은 뺀다."""
        return self._chain(self.gemini_model, self.gemini_fallback_models)

    @property
    def gemini_extraction_models(self) -> list[str]:
        """문서 추출에 시도할 모델 목록."""
        return self._chain(self.gemini_extraction_model, self.gemini_extraction_fallback_models)


@cache
def get_settings() -> Settings:
    return Settings()
