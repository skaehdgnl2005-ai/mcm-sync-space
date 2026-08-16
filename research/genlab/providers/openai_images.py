"""OpenAI 이미지 편집 어댑터 — 06 §4.

★승격 후보: 승자가 되면 이 파일이 `services/gen/app/engines/`로 복사-이식된다.
따라서 import는 **stdlib + openai SDK만** (06 §1 N5). `.base`는 함께 승격되는 짝이다.

`images.edit`는 다중 입력 이미지를 받는다. 06 §4가 정한 순서대로
[사용자 사진, ref들]을 싣고, 그 규약을 provider_meta에 기록한다 (R1 관찰 ③).

**05 §7 리스크 1의 최우선 관측 대상이 이 어댑터다** — 06 §4가 "실인물 편집 정책
거부를 최우선 관찰"로 지목한 프로바이더이므로, refused/error 분류가 틀리면 연구
전체의 결론이 틀어진다.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from openai import AsyncOpenAI

from .base import GenCall, GenOutcome, Stopwatch

_DEFAULT_SIZE = "1152x1536"
"""정확히 3:4. gpt-image-2의 제약(각 변 16의 배수 · 65.5만~829만 px · 비율 ≤ 3:1)을
만족한다. 05 §2의 "출력 3:4 세로" 통제 조건이 근거."""

_DEFAULT_QUALITY = "mid"

_QUALITY_ALIASES = {"mid": "medium", "med": "medium"}
"""models.yaml의 어휘를 프로바이더 문법으로 번역한다 (06 §4 "어댑터가 번역").
레지스트리는 05·06 문서와 같은 'mid'를 쓰고, API는 'medium'을 받는다."""

_OUTPUT_FORMAT = "jpeg"
"""Gemini 쪽 출력이 jpeg 고정(지정 가능한 유일한 MIME)이라 여기서도 jpeg로 맞춘다.
한쪽만 무손실 PNG면 c1(패턴·로고 재현) 채점에 압축 차이가 섞여 들어간다."""

_REFUSAL_CODES = {
    "moderation_blocked",
    "content_policy_violation",
    "image_generation_user_error",
    "safety_violation",
}
"""OpenAI가 명시적으로 내려 주는 거부 코드. 문자열 표지보다 이쪽이 우선한다."""

_REFUSAL_MARKERS = (
    "safety system",
    "content policy",
    "moderation",
    "not allowed",
    "rejected as a result",
    "cannot be edited",
    "policy",
    "violat",
    "real person",
    "identifiable",
    "likeness",
    "public figure",
    "celebrity",
)
"""문구 단위 표지만 둔다 — 낱말 'person'·'face'를 넣으면 프롬프트 원문이 400
메시지에 되울릴 때 설정 오류가 refused로 둔갑한다 (google_genai.py와 동일한 이유)."""

_RATE_LIMIT_MARKERS = ("rate limit", "quota", "too many requests", "insufficient_quota")


class OpenAIImagesClient:
    name = "openai_images"

    def __init__(self) -> None:
        # max_retries=0: 재시도 정책은 **러너 소유**다 (06 §5-6 — 네트워크 오류만 1회,
        # refused는 0회). SDK가 몰래 2회 더 때리면 과금이 3배가 되고 latency_ms가
        # 재시도 시간을 포함해 버려 p95 게이트(05 §5)가 오염된다.
        self._client = AsyncOpenAI(max_retries=0)

    async def generate(self, call: GenCall) -> GenOutcome:
        watch = Stopwatch()
        params = dict(call.params)
        quality = str(params.pop("quality", _DEFAULT_QUALITY)).lower()
        quality = _QUALITY_ALIASES.get(quality, quality)
        size = str(params.pop("size", _DEFAULT_SIZE))

        images: list[tuple[str, bytes, str]] = [
            ("user_photo.jpg", call.user_photo, "image/jpeg")
        ]
        images += [
            (f"product_ref_{i}.jpg", data, "image/jpeg")
            for i, data in enumerate(call.product_refs, start=1)
        ]

        meta: dict[str, Any] = {
            "input_order": "user_photo, product_refs...",
            "ref_count": len(call.product_refs),
            "requested": {
                "size": size,
                "quality": quality,
                "output_format": _OUTPUT_FORMAT,
            },
        }

        try:
            response = await self._client.images.edit(
                model=call.model_id,
                image=images,
                prompt=call.prompt,
                quality=quality,
                size=size,
                output_format=_OUTPUT_FORMAT,
                timeout=call.timeout_s,
                **params,
            )
        except Exception as exc:  # noqa: BLE001 — 분류가 이 어댑터의 존재 이유다
            status, code = _classify_exception(exc)
            return GenOutcome.failed(
                status,
                watch.ms(),
                f"{type(exc).__name__}: {_error_text(exc)}",
                **meta,
                http_status=code,
                error_code=_error_code(exc),
                classified_from="exception",
            )

        meta["usage"] = _usage_dict(response)
        data = getattr(response, "data", None) or []
        b64 = getattr(data[0], "b64_json", None) if data else None
        if b64:
            return GenOutcome.ok(base64.b64decode(b64), watch.ms(), **meta)

        return GenOutcome.failed(
            "error",
            watch.ms(),
            "response contained no image data",
            **meta,
            classified_from="empty_output",
        )


def _classify_exception(exc: Exception) -> tuple[str, "int | None"]:
    """예외 → (status, http_status). google_genai.py와 같은 판정 순서를 쓴다:
    타임아웃 → 레이트리밋 → 거부 → 그 외 error.

    레이트리밋을 거부보다 **먼저** 보는 이유: 429는 재시도 가치가 있는 오류인데
    refused로 분류하면 러너가 재시도를 건너뛰고(06 §5-6), 동시에 리스크 1의
    거부율 통계가 부풀려진다.
    """
    name = type(exc).__name__
    text = _error_text(exc)
    code = _status_code(exc)

    if "Timeout" in name or isinstance(exc, TimeoutError):
        return "timeout", code
    if code == 429 or "RateLimit" in name or _hit(text, _RATE_LIMIT_MARKERS):
        return "error", code
    if _error_code(exc) in _REFUSAL_CODES:
        return "refused", code
    if code in (400, 403, 422) and _hit(text, _REFUSAL_MARKERS):
        return "refused", code
    return "error", code


def _error_code(exc: Exception) -> "str | None":
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return code
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return None


def _status_code(exc: Exception) -> "int | None":
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    match = re.search(r"\b([45]\d{2})\b", str(exc))
    return int(match.group(1)) if match else None


def _hit(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _error_text(exc: Exception) -> str:
    return str(getattr(exc, "message", None) or exc)[:800]


def _usage_dict(response: object) -> dict:
    """실측 토큰 = models.yaml의 cost_per_image_usd를 갱신할 근거 (05 §6)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        key: getattr(usage, key, None)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


def build() -> OpenAIImagesClient:
    return OpenAIImagesClient()
