"""MockEngine — 03-mock-service-spec.md §5.

실제 이미지 생성 없이 계약대로만 동작한다. 외부 API·스토리지 의존 0.
결과 이미지는 이 서비스가 /static으로 직접 서빙하는 샘플 파일이다.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from .base import EngineRequest, EngineResult, GenerationEngine

log = logging.getLogger(__name__)

_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})

SCENARIO_HEADER = "X-Mock-Scenario"
KNOWN_SCENARIOS = frozenset({"success", "fail", "timeout", "slow"})

_QUEUE_SECONDS = 1.0
"""queued 유지 시간. B가 queued 상태를 실제로 관측할 수 있도록 둔다."""


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        log.warning("env %s=%r is not a number; falling back to %s", name, raw, default)
        return default
    if value < 0:
        log.warning("env %s=%r is negative; falling back to %s", name, raw, default)
        return default
    return value


def normalize_scenario(raw: Optional[str]) -> str:
    """미정의 값·누락은 success로 처리한다 (§5)."""
    value = (raw or "").strip().lower()
    return value if value in KNOWN_SCENARIOS else "success"


class MockEngine(GenerationEngine):
    name = "mock"

    def __init__(self, samples_dir: Path, static_url_path: str = "/static/samples"):
        self._samples = sorted(
            path.name
            for path in samples_dir.iterdir()
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES
        )
        if not self._samples:
            # 조용한 placeholder 생성 금지 — 명확한 에러로 기동 실패시킨다 (§5).
            raise RuntimeError(
                f"MockEngine requires at least one sample image in {samples_dir}. "
                "Commit a vertical 3:4 image as static/samples/sample_1.jpg."
            )

        self._static_url_path = "/" + static_url_path.strip("/")
        self._delay_seconds = _env_float("MOCK_DELAY_SECONDS", 5.0)
        self._slow_seconds = _env_float("MOCK_SLOW_SECONDS", 75.0)

        log.info(
            "MockEngine ready: samples=%s, MOCK_DELAY_SECONDS=%s, MOCK_SLOW_SECONDS=%s",
            self._samples,
            self._delay_seconds,
            self._slow_seconds,
        )

    def timeout_seconds(self, request: EngineRequest) -> Optional[float]:
        if normalize_scenario(request.scenario) == "slow":
            # slow는 60초 규약의 유일한 예외 (§2-5) — 하드 타임아웃을 걸지 않는다.
            return None
        return _QUEUE_SECONDS + self._delay_seconds + 10.0

    async def generate(self, request: EngineRequest) -> EngineResult:
        scenario = normalize_scenario(request.scenario)
        raw_scenario = (request.scenario or "").strip()
        if raw_scenario and raw_scenario.lower() not in KNOWN_SCENARIOS:
            log.info(
                "job %s: unknown %s=%r; treating as success",
                request.job_id,
                SCENARIO_HEADER,
                raw_scenario,
            )
        log.info(
            "job %s mock generate: scenario=%s product=%s photo=%s",
            request.job_id,
            scenario,
            request.payload.product.id,
            request.payload.user_photo_url,
        )

        await asyncio.sleep(_QUEUE_SECONDS)
        request.on_processing()

        delay = self._slow_seconds if scenario == "slow" else self._delay_seconds
        await asyncio.sleep(delay)

        if scenario == "fail":
            return EngineResult.fail("UPSTREAM_ERROR")
        if scenario == "timeout":
            return EngineResult.fail("TIMEOUT")
        return EngineResult.ok(self._sample_url(request))

    def _sample_url(self, request: EngineRequest) -> str:
        index = (
            int(hashlib.sha256(request.job_id.encode()).hexdigest(), 16)
            % len(self._samples)
        )
        base = request.base_url.rstrip("/")
        return f"{base}{self._static_url_path}/{self._samples[index]}"
