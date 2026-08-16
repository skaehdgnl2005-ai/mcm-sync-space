"""프로바이더 공통 인터페이스 — 06-gen-research-tools-spec.md §4.

★승격 경로 위: 승자 어댑터(`providers/{winner}.py`)가 이 파일의 dataclass를 쓰므로
이 파일도 함께 복사-이식된다. 따라서 **import는 stdlib만** (06 §1 N5).
genlab 내부 모듈·yaml·pillow를 여기서 import하면 승격 시 딸려간다.

bytes-in / bytes-out 이 계약의 핵심이다. 파일 읽기(연구 러너)와 URL 다운로드
(실엔진)는 호출자 책임이고, 어댑터 코어는 양쪽에서 그대로 돈다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Protocol

GenStatus = Literal["ok", "refused", "error", "timeout"]
"""`refused`(정책 거부) vs `error`(전송·업스트림 실패)의 구분은 어댑터 책임이다.
이 분류가 05 §7 리스크 1(실인물 편집 정책 거부)의 유일한 데이터 원천이다."""


@dataclass(frozen=True)
class GenCall:
    prompt: str
    user_photo: bytes
    """역할 명명 — 순서 리스트 금지. 프로바이더마다 다중 이미지 규약이 달라
    '어느 이미지가 사람인가'가 명시돼야 어댑터가 올바르게 조립할 수 있다."""
    product_refs: list[bytes]
    """1~3장."""
    model_id: str
    """models.yaml의 문자열 그대로 통과 — 코드가 모델명을 알지 않는다 (06 §3)."""
    params: dict
    """어댑터가 프로바이더 문법으로 번역한다 (예: quality=mid)."""
    timeout_s: float = 50.0


@dataclass(frozen=True)
class GenOutcome:
    status: GenStatus
    image: bytes | None
    latency_ms: int
    error_message: str | None = None
    provider_meta: dict = field(default_factory=dict)

    @classmethod
    def ok(cls, image: bytes, latency_ms: int, **meta: object) -> "GenOutcome":
        return cls("ok", image, latency_ms, provider_meta=dict(meta))

    @classmethod
    def failed(
        cls,
        status: GenStatus,
        latency_ms: int,
        error_message: str,
        **meta: object,
    ) -> "GenOutcome":
        return cls(status, None, latency_ms, error_message, dict(meta))


class ProviderClient(Protocol):
    name: str

    async def generate(self, call: GenCall) -> GenOutcome: ...


class Stopwatch:
    """어댑터가 지연을 재는 표준 방법. `GenOutcome.latency_ms`는 항상 채운다
    (실패 레코드의 지연도 R4 실패 분류의 입력이다)."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)


_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF8", "gif"),
)


def sniff_image_ext(data: bytes, default: str = "jpg") -> str:
    """매직 바이트로 확장자 판정. 프로바이더가 png를 돌려주는데 .jpg로 저장하면
    갤러리·후처리에서 조용히 깨지므로, 저장 확장자는 내용으로 정한다."""
    for magic, ext in _IMAGE_MAGIC:
        if data.startswith(magic):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return default
