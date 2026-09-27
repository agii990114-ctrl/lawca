"""Gemini로 법원 문서 PDF를 읽어 CourtDocument로 추출한다."""

from __future__ import annotations

from typing import Protocol

from google import genai
from google.genai import errors, types

from lawca.extraction.schema import CourtDocument

PROMPT = """\
첨부한 PDF는 한국 법원이 보낸 문서입니다. 아래 규칙에 따라 정보를 추출하세요.

- 문서에 적힌 내용만 추출합니다. 추측하거나 보충하지 않습니다. 문서에 없는 항목은 null로 둡니다.
- 모든 값에는 근거를 붙입니다. quote는 원문 문구를 고치지 말고 그대로 옮기고(60자 이내), page는 1부터 센 쪽 번호입니다.
- 송달일은 문서에 적혀 있지 않은 정보이므로 추출하지 않습니다. 작성일·발령일·선고일만 issued_date로 추출합니다.
- designated_period는 문서가 기간을 정한 경우에만 채웁니다. 예: "이 명령을 송달받은 날부터 7일 이내" → amount 7, unit "일".
- hearing은 문서가 출석할 기일(변론기일·조정기일 등)을 알리는 경우에만 채웁니다. 날짜는 YYYY-MM-DD, 시각은 HH:MM(24시간)입니다. 작성일을 기일로 적지 않습니다.
- 당사자는 문서에 적힌 지위(원고, 피고, 채권자, 채무자 등)와 이름을 그대로 적습니다.
- 문서 종류가 목록에 없으면 "기타"로 둡니다.
"""


class ExtractionError(RuntimeError):
    """LLM 응답을 스키마에 맞게 해석하지 못했을 때 발생한다."""


class ModelUnavailableError(RuntimeError):
    """모델 서버가 혼잡(503)하거나 사용량 한도(429)에 걸렸을 때 발생한다. 다음 모델로 넘기거나 잠시 뒤 다시 시도한다."""


class Extractor(Protocol):
    model: str
    """마지막 추출에 실제로 쓴 모델."""

    def extract(self, pdf: bytes) -> CourtDocument: ...


class GeminiExtractor:
    """models를 순서대로 시도한다. 뒤의 모델은 앞의 모델 서버가 혼잡할 때만 쓴다."""

    def __init__(self, api_key: str, models: list[str]) -> None:
        if not models:
            raise ValueError("모델을 하나 이상 지정해야 합니다.")
        self._client = genai.Client(api_key=api_key)
        self._models = models
        self.model = models[0]

    def extract(self, pdf: bytes) -> CourtDocument:
        failures: list[ModelUnavailableError] = []
        for model in self._models:
            try:
                doc = self._extract_with(model, pdf)
            except ModelUnavailableError as exc:
                failures.append(exc)
                continue
            self.model = model
            return doc
        if len(failures) == 1:
            raise failures[0]
        raise ModelUnavailableError("모든 모델을 쓸 수 없습니다. " + " / ".join(str(e) for e in failures))

    def _extract_with(self, model: str, pdf: bytes) -> CourtDocument:
        try:
            response = self._client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=pdf, mime_type="application/pdf"), PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=CourtDocument,
                    temperature=0,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except errors.ServerError as exc:
            raise ModelUnavailableError(f"{model} 서버가 응답하지 않습니다({exc.code}). 잠시 뒤 다시 시도하세요.") from exc
        except errors.ClientError as exc:
            if exc.code == 429:
                raise ModelUnavailableError(f"{model} 사용량 한도를 넘었습니다(429). 잠시 뒤 다시 시도하세요.") from exc
            raise ExtractionError(f"{model} 요청이 거부되었습니다({exc.code}): {exc.message}") from exc
        if isinstance(response.parsed, CourtDocument):
            return response.parsed
        try:
            return CourtDocument.model_validate_json(response.text or "")
        except ValueError as exc:
            raise ExtractionError(f"추출 결과를 해석하지 못했습니다: {exc}") from exc
