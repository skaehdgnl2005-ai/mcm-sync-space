"""Google AI (Gemini) 이미지 편집 어댑터 — 06 §4.

★승격 후보: 승자가 되면 이 파일이 `services/gen/app/engines/`로 복사-이식된다.
따라서 import는 **stdlib + google-genai SDK만** (06 §1 N5). yaml·genlab 내부 모듈
금지. `.base`는 함께 승격되는 짝이라 예외다.

Interactions API를 쓴다 (`client.aio.interactions.create`). generateContent는 현재
문서상 legacy이고, 종횡비·출력 해상도를 `response_format`으로 **명시**할 수 있는
쪽이 Interactions다 — 05 §2의 "출력 3:4 세로" 통제 조건이 여기에 걸려 있다.

**refused vs error 분류가 이 파일의 핵심 책임이다** (06 §4). 05 §7 리스크 1
("실인물 얼굴 편집 정책 거부")의 유일한 데이터 원천이므로, 분류 근거가 된 원시
신호를 provider_meta에 남긴다 — R1에서 사람이 재확인할 수 있어야 한다.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from google import genai

from .base import GenCall, GenOutcome, Stopwatch

_DEFAULT_ASPECT_RATIO = "3:4"
"""05 §2 통제 조건. Interactions의 aspect_ratio가 '3:4'를 직접 지원한다."""

_DEFAULT_IMAGE_SIZE = "1K"
"""출력 해상도 티어. 1K ≈ 1MP로 단가 최저 구간이며, 화보 채점에는 충분하다."""

_OUTPUT_MIME = "image/jpeg"
"""Interactions가 지정 가능한 유일한 출력 MIME. gpt 어댑터도 jpeg로 맞춰
'한쪽만 무손실'이라는 교란 변수를 없앤다."""

_REFUSAL_MARKERS = (
    "safety",
    "blocked",
    "block_reason",
    "policy",
    "prohibited",
    "not allowed",
    "cannot generate",
    "can't generate",
    "unable to generate",
    "cannot create",
    "can't create",
    "will not",
    "content filter",
    "violat",
    "responsible ai",
    "real person",
    "identifiable",
    "likeness",
    "celebrity",
    "public figure",
)
"""4xx 본문·모델 응답 텍스트에서 '정책 거부'로 볼 표지.

넓게 잡는 이유: 거부를 error로 잘못 분류하면 러너가 **재시도**해 버린다 —
정책 거부를 재시도로 덮는 것은 06 §5-6이 금지한 행동이고, 리스크 1의 측정값도
망가진다. 반대 방향의 오분류(=error를 refused로 봄)는 재시도를 한 번 잃을 뿐이다.
따라서 의심스러우면 refused 쪽으로 기운다.

단, 표지는 **구(phrase) 단위**로만 둔다. 'person'·'face' 같은 낱말을 넣으면
프롬프트 원문이 에러 메시지에 되울릴 때(우리 프롬프트는 통째로 인물 편집 지시다)
설정 오류가 refused로 둔갑한다 — 실제로 첫 스모크의 400이 그 경로였다."""

_RATE_LIMIT_MARKERS = ("rate limit", "quota", "resource_exhausted", "too many requests")
"""429 계열은 정책 거부가 아니라 **재시도 가치가 있는 오류**다 (05 §7 레이트리밋).
_REFUSAL_MARKERS보다 먼저 검사해야 'quota exceeded ... policy' 같은 문구가
refused로 새지 않는다."""


class GoogleGenAIClient:
    name = "google_genai"

    def __init__(self) -> None:
        # API 키는 env(GOOGLE_API_KEY)에서만 읽는다 (06 §1 N6).
        self._client = genai.Client()

    async def generate(self, call: GenCall) -> GenOutcome:
        watch = Stopwatch()
        params = dict(call.params)
        meta: dict[str, Any] = {
            # R1 관찰 ③(다중 이미지 규약)의 기록. 06 §4가 정한 순서를 그대로 쓴다.
            "input_order": "user_photo, product_refs..., prompt",
            "ref_count": len(call.product_refs),
        }

        image_inputs = [call.user_photo, *call.product_refs]
        input_resolution = params.pop("input_resolution", None)
        contents: list[dict[str, Any]] = []
        for data in image_inputs:
            part: dict[str, Any] = {
                "type": "image",
                "data": base64.b64encode(data).decode("ascii"),
                "mime_type": "image/jpeg",
            }
            if input_resolution:  # 미지원 모델에 보내면 400 — _INPUT_RESOLUTION_NOTE 참조
                part["resolution"] = input_resolution
            contents.append(part)
        contents.append({"type": "text", "text": call.prompt})

        response_format = {
            "type": "image",
            "aspect_ratio": params.pop("aspect_ratio", _DEFAULT_ASPECT_RATIO),
            "image_size": params.pop("image_size", _DEFAULT_IMAGE_SIZE),
            "mime_type": _OUTPUT_MIME,
        }
        meta["requested"] = dict(response_format)
        meta["input_resolution"] = input_resolution

        try:
            interaction = await self._client.aio.interactions.create(
                model=call.model_id,
                input=contents,
                response_format=response_format,
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
                classified_from="exception",
            )

        meta["interaction_status"] = getattr(interaction, "status", None)
        meta["usage"] = _usage_dict(interaction)

        image = getattr(interaction, "output_image", None)
        data = getattr(image, "data", None) if image is not None else None
        if data:
            meta["output_mime"] = getattr(image, "mime_type", None)
            return GenOutcome.ok(base64.b64decode(data), watch.ms(), **meta)

        # 이미지 없이 돌아온 경우 — 모델이 말로 거절했거나, 응답이 실패·중단됐다.
        text = (getattr(interaction, "output_text", None) or "").strip()
        meta["output_text"] = text[:500]
        status = (
            "refused"
            if _hit(text, _REFUSAL_MARKERS) or meta["interaction_status"] == "failed"
            else "error"
        )
        return GenOutcome.failed(
            status,
            watch.ms(),
            f"no image in response (status={meta['interaction_status']}): "
            f"{text[:200] or '<no text>'}",
            **meta,
            classified_from="empty_output",
        )


def _classify_exception(exc: Exception) -> tuple[str, "int | None"]:
    """예외 → (status, http_status).

    SDK의 예외 **클래스**가 아니라 HTTP 상태 코드와 메시지로 판정한다.
    Interactions 클라이언트는 `google.genai.errors`와 무관한 자체 예외 계층
    (`GeminiNextGenAPIClientError`)을 쓰는데 그 경로가 비공개(`_interactions`)이고,
    실험적 API라 이름이 바뀔 수 있다. 승격 대상 파일이 비공개 심볼에 묶이면
    SDK 업그레이드가 곧 배포 장애가 된다 — 상태 코드는 그보다 오래 간다.
    """
    name = type(exc).__name__
    text = _error_text(exc)
    code = _status_code(exc)

    if "Timeout" in name or isinstance(exc, TimeoutError):
        return "timeout", code
    # 레이트리밋을 refused로 오분류하면 재시도 기회를 잃고 리스크 1 통계가 오염된다.
    if code == 429 or "RateLimit" in name or _hit(text, _RATE_LIMIT_MARKERS):
        return "error", code
    if code in (400, 403, 422) and _hit(text, _REFUSAL_MARKERS):
        return "refused", code
    return "error", code


def _status_code(exc: Exception) -> "int | None":
    for attr in ("status_code", "code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    match = re.search(r"\b([45]\d{2})\b", str(exc))
    return int(match.group(1)) if match else None


def _hit(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _error_text(exc: Exception) -> str:
    message = getattr(exc, "message", None)
    return str(message or exc)[:800]


def _usage_dict(interaction: object) -> dict:
    """실측 토큰 = models.yaml의 cost_per_image_usd를 갱신할 근거 (05 §6)."""
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return {}
    return {
        key: getattr(usage, key, None)
        for key in ("total_input_tokens", "total_output_tokens", "total_tokens")
    }


def build() -> GoogleGenAIClient:
    return GoogleGenAIClient()
