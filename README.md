# lawca

법무법인 사무원용 송무 AI 에이전트입니다. 법원 문서 PDF를 올리거나 요청을 입력하면 할 일과 기한을 정리하고, 대응 서류 초안까지 만듭니다.

> 현재 상태: 채팅형 화면(Claude·ChatGPT 방식)에서 법원 문서 PDF를 첨부하면 추출 → 송달일 입력 → 기한 계산 → 할 일이 동작합니다. 글 요청은 LangGraph 라우터가 분류해 기한·사건 조회, 만료일 계산, 서식 작성(확정증명원·송달증명원 신청서, 주소보정서)을 처리합니다. 서식에 필요한 정보가 없으면 입력 폼으로 되묻고, 답하면 멈춘 곳에서 이어서 DOCX 초안을 만듭니다. 대화, 첨부 파일, 사건, 문서, 확정한 기한은 PostgreSQL에 저장합니다. 확정한 기한은 기한 화면에서 모아 보고 캘린더(ICS)·엑셀(CSV)로 내보냅니다.

## 문서

- [기획서](docs/lawca-기획서.md): 기능 범위, 구조, 데이터 모델, 검증 전략, 로드맵, 리스크
- [사무원 안내](docs/lawca-사무원-안내.md): 현직자 검토용으로 만든 안내문(현재는 사용하지 않음)

## 핵심 원칙

- 기한 계산, 필수 항목 누락 판단, 검증은 LLM이 아닌 코드로 처리합니다.
- 추출값에는 원문 근거를 표시하고, 기한 확정과 제출은 사람이 합니다.
- 로직은 순수 함수에 두고, LangGraph 노드는 이를 호출만 합니다.

## 구조

```
backend/    Python(FastAPI, SQLAlchemy, Alembic, LangGraph 예정). 도메인 로직은 src/lawca/ 아래 순수 함수
frontend/   React + TypeScript(Vite)
docs/       기획서
db/         개발용 DB 초기화 스크립트(docker-compose.yml과 함께)
```

## 개발

백엔드는 [uv](https://docs.astral.sh/uv/), 프론트엔드는 Node.js가 필요합니다.

레포 루트 `.env`에 LLM 설정을 넣습니다(커밋되지 않습니다). `.env.example`을 복사해 쓰면 됩니다.

- **Ollama(로컬)**: `LLM_PROVIDER=ollama`. 이 PC(또는 법인 서버)에서 모델을 돌려서 문서가 밖으로 나가지 않고 사용량 한도가 없습니다. 기본 모델은 `gemma4:e4b`(`ollama pull gemma4:e4b`)입니다. GPU 드라이버가 맞지 않으면 `OLLAMA_NUM_GPU=0`으로 CPU만 씁니다. CPU만 쓰면 요청 분류 15초, 조회 1분, 문서 추출 1분 반 정도 걸립니다.
- **Gemini(클라우드)**: `LLM_PROVIDER=gemini`와 `GEMINI_API=발급받은_키`. 요청 분류·서식 해석·조회는 `gemini-3.5-flash-lite`, 문서 추출은 `gemini-3.7-flash`를 씁니다(`.env.example` 참고). 빠르지만 문서가 Google API로 전송되고, 무료 등급은 사용량 한도가 작습니다.

DB(PostgreSQL, Docker)를 띄우고 마이그레이션을 적용합니다. 포트는 5433을 씁니다.

```bash
docker compose up -d --wait
cd backend && uv sync && uv run alembic upgrade head
```

```bash
cd backend && uv run pytest
cd backend && uv run uvicorn lawca.api.app:app --port 8000
cd frontend && npm install && npm run dev
```

- 테스트는 같은 컨테이너의 `lawca_test` 데이터베이스를 씁니다. DB가 없으면 DB 테스트는 건너뜁니다.
- 테이블을 바꾸면 `uv run alembic revision --autogenerate -m "설명"`으로 마이그레이션을 만듭니다. `alembic.ini`는 Windows에서 cp949로 읽히므로 한글을 넣지 않습니다.

브라우저에서 `http://localhost:5173`을 열고, 입력창에 PDF를 끌어다 놓거나 📎로 첨부합니다. 프론트엔드 개발 서버는 `/api` 요청을 백엔드(`localhost:8000`)로 넘깁니다.

- Gemini를 쓸 때 서버가 혼잡(503)하거나 사용량 한도(429)에 걸리면 예비 모델을 차례로 시도하고, 화면에 실제로 쓴 모델을 표시합니다.
- Gemini를 쓰는 동안에는 실제 의뢰인 문서를 올리지 않습니다. 문서가 Gemini API로 전송됩니다. 동작 확인용 가상 문서는 `backend/tests/fixtures/`에 있습니다(`backend/scripts/make_sample_pdf.py`로 생성).

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

### 서식 (`lawca.forms`)

- 서식마다 항목(필수 여부, 기본값, 사건에서 가져올 값, 나중에 입력 허용, 기억 여부)을 `backend/src/lawca/forms/data/forms.toml`에 정의합니다.
- DOCX 템플릿은 `backend/scripts/make_form_templates.py`로 만듭니다. 법인이 쓰던 양식으로 바꾸려면 같은 자리표시자(`{{ case_number }}` 등)를 넣은 DOCX로 교체합니다.
- 서식 문구는 공개 양식의 일반적인 구성을 참고했으며 공식 양식과 한 글자씩 대조하지 않았습니다. 제출 전에 전자소송 양식과 비교합니다.
- 되묻기로 멈춘 작업은 PostgreSQL 체크포인터(LangGraph)로 저장되어 서버를 다시 띄워도 이어갈 수 있습니다.

### 추출 평가 (`backend/eval`)

- `eval/make_documents.py`: 가상 사건으로 합성 법원 문서 16건(텍스트 PDF 10, 스캔본 6)과 정답 JSON을 만듭니다. 판결의 변론종결일·선고일, 기일통지서의 작성일·기일처럼 헷갈리기 쉬운 날짜를 함께 넣었습니다.
- `eval/run.py`: 추출 결과를 정답과 항목별로 비교해 `eval/results/`에 보고서(md·json)를 씁니다. 기본은 로컬 Ollama이고, Gemini는 `--provider gemini --allow-api`를 줘야 돌아갑니다.
- 합성 문서는 실제 법원 문서보다 깨끗하므로 수치는 실제 정확도의 상한으로 봅니다.

```bash
cd backend && uv run python eval/run.py --only scan --limit 3
```

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
