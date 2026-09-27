"""라우터: 글 요청을 작업 목록으로 나눈다. LLM은 분류만 하고, 어느 노드로 갈지는 그래프가 정한다."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from lawca.agent.llm import ChatModel, Turn

Label = Literal["query", "draft", "out_of_scope", "help"]

ROUTER_PROMPT = """\
당신은 법무법인 사무원을 돕는 업무 도구의 요청 분류기입니다. 사용자의 마지막 요청을 작업 목록으로 나눕니다.

라벨
- query: 저장된 기한·사건·문서를 조회하거나, 자료실에서 과거 서면·서식·문서를 찾거나, 송달일과 기간으로 만료일을 계산해 달라는 요청.
  예: "이번 주 기한 알려줘", "홍길동 사건 찾아줘", "2026가단51234 기한은?", "9월 15일 송달이면 항소기한은?"
- draft: 서식이나 서면을 만들거나 고쳐 달라는 요청. 예: "확정증명원 신청서 만들어 줘", "주소보정서 써 줘", "집행문 부여 신청해야 해"
  (예전 서면·서식을 "찾아 줘", "보여 줘"는 query다)
- out_of_scope: 법률 판단·자문 요청. 예: "이길 수 있을까?", "항소하는 게 나을까?", "위자료는 얼마가 적당해?"
- help: 인사, 기능 문의, 무엇을 할지 모호한 요청. 예: "안녕", "뭘 할 수 있어?"

규칙
- 한 문장에 여러 업무가 있으면 업무마다 작업을 하나씩 만듭니다(최대 5개). 순서는 요청에 나온 순서를 따릅니다.
- request에는 그 작업에 필요한 내용만 짧게 적습니다. 앞선 대화에서 가리키는 사건번호 등이 있으면 채워 넣습니다.
- 확신이 없으면 help로 둡니다.
"""


class RoutedTask(BaseModel):
    label: Label
    request: str = Field(description="이 작업에 필요한 요청 내용")


class RouteDecision(BaseModel):
    tasks: list[RoutedTask]


def route_text(model: ChatModel, message: str, history: list[Turn]) -> list[RoutedTask]:
    decision = model.structured(ROUTER_PROMPT, [*history, Turn("user", message)], RouteDecision)
    tasks = decision.tasks[:5]
    return tasks or [RoutedTask(label="help", request=message)]
