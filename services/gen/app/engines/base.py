"""엔진 인터페이스 — 03-mock-service-spec.md §6.

이 기획서에서 구조적으로 가장 중요한 요구사항: 가짜인 부분은 엔진 구현체
하나로 격리한다. 실엔진 투입 = engines/에 GenerationEngine 서브클래스 추가
+ engines/__init__.py 레지스트리 등록 + env ENGINE 변경. 그 외 파일은 불변.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, ClassVar, Optional

from ..schemas import GenerateRequest


@dataclass(frozen=True)
class EngineRequest:
    """엔진 1회 실행에 필요한 전부."""

    job_id: str
    payload: GenerateRequest
    base_url: str
    """샘플/결과 이미지의 절대 URL 조립용 (PUBLIC_BASE_URL 또는 요청 base URL)."""
    scenario: str
    """X-Mock-Scenario 헤더 원본값. mock 전용이며 실엔진은 무시한다 (§5)."""
    on_processing: Callable[[], None]
    """엔진이 실제 작업을 시작하는 순간 호출 → job이 processing으로 전이."""


@dataclass(frozen=True)
class EngineResult:
    """성공은 image_url, 실패는 error_code. 둘 중 하나만 채운다."""

    image_url: Optional[str] = None
    error_code: Optional[str] = None

    @classmethod
    def ok(cls, image_url: str) -> "EngineResult":
        return cls(image_url=image_url)

    @classmethod
    def fail(cls, error_code: str) -> "EngineResult":
        return cls(error_code=error_code)


class GenerationEngine(ABC):
    """(사람 사진 + 확정된 상품 이미지) → 화보 URL 변환기."""

    name: ClassVar[str] = "base"

    default_timeout_seconds: ClassVar[float] = 55.0
    """호출측 하드 타임아웃. 60초 내 done/failed 종결 보장의 마지막 방어선 (§2-5)."""

    @abstractmethod
    async def generate(self, request: EngineRequest) -> EngineResult:
        """예외를 던져도 된다 — 라우트 레이어가 UNKNOWN으로 종결시킨다."""
        raise NotImplementedError

    def timeout_seconds(self, request: EngineRequest) -> Optional[float]:
        """None을 반환하면 하드 타임아웃을 걸지 않는다 (엔진이 자체 종결을 보장할 때만)."""
        return self.default_timeout_seconds
