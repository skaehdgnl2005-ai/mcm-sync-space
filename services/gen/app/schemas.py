"""계약 스키마 — 03-mock-service-spec.md §3~4 / 00-common.md §5.

경고: 이 파일의 필드명·enum 값은 Dev B의 코드와 맞물린 계약이다.
추가·삭제·개명은 두 개발자 합의 + 00-common.md 동시 갱신 없이 금지 (§2-1).
"""

from __future__ import annotations

from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, field_validator

# --- 계약 enum ---------------------------------------------------------------

JobStatus = Literal["queued", "processing", "done", "failed"]
"""상태 전이: queued → processing → done | failed. 역행 금지 (§4)."""

JobErrorCode = Literal["BAD_INPUT", "UPSTREAM_ERROR", "TIMEOUT", "UNKNOWN"]
"""job 실패 사유. GET /generate/{job_id}의 error_code에 쓰이는 값 전체."""

_ALLOWED_URL_SCHEMES = ("http", "https")


def validate_http_url(value: object) -> str:
    """이미지는 항상 공개 http(s) URL로만 주고받는다 (§2-4, base64 금지).

    실패 시 ValueError를 던지고, main.py의 validation 핸들러가
    400 BAD_INPUT + "<어느 필드가 왜>" 메시지로 변환한다.
    """
    if not isinstance(value, str):
        raise ValueError("must be a string containing an http(s) URL")

    raw = value.strip()
    if not raw:
        raise ValueError("must not be empty")

    scheme = urlparse(raw).scheme.lower()
    if scheme == "data" or raw[:5].lower() == "data:":
        raise ValueError(
            "must not be a data: (base64) URL; images are passed by URL only"
        )
    if scheme not in _ALLOWED_URL_SCHEMES or not urlparse(raw).netloc:
        raise ValueError(f"must be an absolute http(s) URL (got {raw!r})")
    return raw


# --- 요청 -------------------------------------------------------------------


class Product(BaseModel):
    """생성 대상 상품. 매칭은 B가 이미 끝냈고, A는 확정된 1개만 받는다.

    미정의 키(v1의 `pattern` 등)는 pydantic 기본 동작으로 조용히 무시된다.
    """

    id: str
    """MCM SKU (예: MMRGATA04CO001). 형식 검증은 하지 않는다."""
    name: Optional[str] = None
    category: Optional[str] = None
    material: Optional[str] = None
    """DB '소재 구성' 원문 텍스트."""
    color_hardware: Optional[str] = None
    """DB '컬러&하드웨어' 원문 텍스트."""
    wear_position: str
    """착용 위치 (hand·shoulder·cross·back·neck 등). 필수, 값 자체는 enum 검증하지 않는다."""
    ref_image_urls: list[str]

    @field_validator("id", "wear_position")
    @classmethod
    def _must_be_present(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()

    @field_validator("ref_image_urls")
    @classmethod
    def _refs_must_be_urls(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("must contain at least one http(s) URL")
        return [validate_http_url(item) for item in value]


class StyleHints(BaseModel):
    """선택 힌트. 미정의 slug·부분 생략·전체 생략 모두 통과시킨다 (§3 검증 규칙).

    알 수 없는 키(v1의 `tpo` 등)는 에러 없이 버린다.
    """

    model_config = ConfigDict(extra="ignore")

    city: Optional[str] = None
    purpose: Optional[str] = None
    lighting: Optional[str] = None


class GenerateRequest(BaseModel):
    user_photo_url: str
    product: Product
    style_hints: Optional[StyleHints] = None

    @field_validator("user_photo_url")
    @classmethod
    def _photo_must_be_url(cls, value: str) -> str:
        return validate_http_url(value)


# --- 응답 -------------------------------------------------------------------


class GenerateAcceptedResponse(BaseModel):
    """POST /generate → 202 (200 아님, §2-2)."""

    job_id: str


class JobStatusResponse(BaseModel):
    """GET /generate/{job_id} → 200.

    네 키는 항상 직렬화된다. 해당 없으면 null이며 키를 생략하지 않는다 (§2-3).
    """

    status: JobStatus
    image_url: Optional[str] = None
    error_code: Optional[JobErrorCode] = None
    elapsed_ms: int


class ErrorResponse(BaseModel):
    """400 / 401 / 404 공통 에러 바디."""

    error_code: str
    message: str
