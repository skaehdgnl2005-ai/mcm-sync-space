"""fake 프로바이더 — API 키·과금 없이 파이프라인 전체를 검증한다 (06 §1 F8).

입력 셀카 bytes를 그대로 돌려준다. 러너→갤러리→리포트 왕복을 $0로 돌리는 것이
목적이며, 키 발급 지연이 툴 개발을 블로킹하지 않게 하는 장치다.
승격 대상 아님.
"""

from __future__ import annotations

import asyncio

from .base import GenCall, GenOutcome, Stopwatch


class FakeClient:
    name = "fake"

    async def generate(self, call: GenCall) -> GenOutcome:
        watch = Stopwatch()
        # 실제 지연 대신 최소 sleep — 동시성 세마포어가 실제로 동작하는지 관측 가능하게.
        await asyncio.sleep(0.05)
        return GenOutcome.ok(
            call.user_photo,
            watch.ms(),
            fake=True,
            prompt_chars=len(call.prompt),
            refs=len(call.product_refs),
        )


def build() -> FakeClient:
    return FakeClient()
