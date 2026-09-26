"""설정. 레포 루트의 .env와 backend/.env를 읽는다."""

from __future__ import annotations

from functools import cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str | None = Field(default=None, validation_alias=AliasChoices("GEMINI_API", "GEMINI_API_KEY"))
    # 별칭(gemini-flash-latest 등)은 가리키는 모델이 바뀌어 평가를 재현할 수 없으므로 버전을 고정한다.
    gemini_model: str = Field(default="gemini-3.7-flash", validation_alias="GEMINI_MODEL")
    # 앞 모델 서버가 혼잡(503)할 때만 차례로 쓰는 예비 모델(쉼표로 구분). 응답에는 실제로 쓴 모델을 기록한다.
    # 마지막의 gemini-flash-latest는 별칭이라 최후의 수단으로만 둔다.
    gemini_fallback_models: str = Field(
        default="gemini-3.5-flash,gemini-flash-latest", validation_alias="GEMINI_FALLBACK_MODELS"
    )
    max_upload_mb: int = 20


    @property
    def gemini_models(self) -> list[str]:
        """시도할 모델 목록. 기본 모델이 먼저이고 중복은 뺀다."""
        models = [self.gemini_model, *(m.strip() for m in self.gemini_fallback_models.split(","))]
        return list(dict.fromkeys(m for m in models if m))


@cache
def get_settings() -> Settings:
    return Settings()
