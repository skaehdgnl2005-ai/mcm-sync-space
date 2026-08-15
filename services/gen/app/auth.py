"""X-API-Key 인증 — 03-mock-service-spec.md §2-6.

/health를 제외한 모든 엔드포인트에 적용된다.
불일치·누락 → 401 {"error_code":"UNAUTHORIZED","message":"..."}
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException

API_KEY_HEADER = "X-API-Key"
API_KEY_ENV = "API_KEY"


def load_api_key() -> str:
    """env API_KEY를 읽는다. 미설정이면 RuntimeError (기동 실패용, §7)."""
    key = (os.getenv(API_KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(
            f"{API_KEY_ENV} env var is required but missing/empty. "
            "Set it to the same value Dev B uses as GEN_API_KEY, then restart."
        )
    return key


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias=API_KEY_HEADER),
) -> None:
    """라우트 의존성. 본문 검증보다 먼저 평가되므로 401이 400보다 우선한다."""
    expected = load_api_key()
    provided = (x_api_key or "").strip()

    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail={
                "error_code": "UNAUTHORIZED",
                "message": f"missing or invalid {API_KEY_HEADER} header",
            },
        )
