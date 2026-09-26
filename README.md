# lawca

법무법인 사무원용 송무 AI 에이전트입니다. 법원 문서 PDF를 올리거나 요청을 입력하면 할 일과 기한을 정리하고, 대응 서류 초안까지 만듭니다.

> 현재 상태: 채팅형 화면(Claude·ChatGPT 방식)에서 법원 문서 PDF를 첨부하면 추출 → 송달일 입력 → 기한 계산 → 할 일이 동작합니다. 글로 하는 요청(서식 작성, 조회 등)은 LangGraph 라우터를 붙이는 단계에서 추가합니다. 저장은 아직 메모리입니다.

## 문서

- [기획서](docs/lawca-기획서.md): 기능 범위, 구조, 데이터 모델, 검증 전략, 로드맵, 리스크
- [사무원 안내](docs/lawca-사무원-안내.md): 현직자 검토용으로 만든 안내문(현재는 사용하지 않음)

## 핵심 원칙

- 기한 계산, 필수 항목 누락 판단, 검증은 LLM이 아닌 코드로 처리합니다.
- 추출값에는 원문 근거를 표시하고, 기한 확정과 제출은 사람이 합니다.
- 로직은 순수 함수에 두고, LangGraph 노드는 이를 호출만 합니다.

## 구조

```
backend/    Python(FastAPI, LangGraph 예정). 도메인 로직은 src/lawca/ 아래 순수 함수
frontend/   React + TypeScript(Vite)
docs/       기획서
```

## 개발

백엔드는 [uv](https://docs.astral.sh/uv/), 프론트엔드는 Node.js가 필요합니다.

레포 루트 `.env`에 Gemini API 키를 넣습니다(커밋되지 않습니다).

```
GEMINI_API=발급받은_키
# 선택: GEMINI_MODEL=gemini-3.7-flash, GEMINI_FALLBACK_MODELS=gemini-3.5-flash,gemini-flash-latest
```

```bash
cd backend && uv sync && uv run pytest
cd backend && uv run uvicorn lawca.api.app:app --port 8000
cd frontend && npm install && npm run dev
```

브라우저에서 `http://localhost:5173`을 열고, 입력창에 PDF를 끌어다 놓거나 📎로 첨부합니다. 프론트엔드 개발 서버는 `/api` 요청을 백엔드(`localhost:8000`)로 넘깁니다.

- Gemini 서버가 혼잡(503)하면 예비 모델을 차례로 시도하고, 화면에 실제로 쓴 모델을 표시합니다.
- 개발 중에는 실제 의뢰인 문서를 올리지 않습니다. 문서가 Gemini API로 전송됩니다. 동작 확인용 가상 문서는 `backend/tests/fixtures/`에 있습니다(`backend/scripts/make_sample_pdf.py`로 생성).

### 기한 계산 (`lawca.deadlines`)

```python
from datetime import date
from lawca.deadlines import compute_statutory_deadline, compute_designated_deadline, Period, Unit

compute_statutory_deadline("appeal", date(2026, 9, 1)).deadline          # 항소: 2026-09-15
compute_designated_deadline(date(2026, 9, 1), Period(7, Unit.DAY)).deadline  # 보정기간 7일: 2026-09-08
```

- 법정 기간 규칙은 `backend/src/lawca/deadlines/data/rules.toml`에 근거 조문, 조문 발췌, 법령 버전, 확인일과 함께 둡니다.
- 공휴일은 `backend/src/lawca/deadlines/data/kr_holidays.csv`(2024~2028년)에 둡니다. `backend/scripts/generate_holidays.py`로 다시 만들 수 있고, 라이브러리가 모르는 임시공휴일은 직접 추가합니다. 데이터가 없는 연도는 계산을 거부합니다.
- 전자소송 간주 송달은 간주 송달일과 초일 산입 여부를 판단하지 않고 경고만 붙입니다.

## 기술 스택(계획)

Python · FastAPI · LangGraph · Gemini Flash · PostgreSQL(pgvector) · React

## 로드맵

0. 준비: 공개 판결문·합성 평가 세트, 조문 근거가 달린 기간 규칙 표
1. 기한 계산 모듈
2. 얇은 끝-끝 흐름(PDF → 추출 → 송달일 → 기한 → 체크리스트)
3. 서식 초안과 되묻기
4. 서식 검색(RAG)
5. 프롬프트 창과 라우터
6. 자체 시험

자세한 내용은 기획서 9장을 보세요.
