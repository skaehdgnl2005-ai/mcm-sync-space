"""인메모리 job store + 상태 전이 — 03-mock-service-spec.md §1, §4.

프로세스 재시작 시 소실 허용. DB·Redis·큐 없음.
상태 전이는 여기서만 일어난다. 엔진은 결과(EngineResult)만 돌려주고
전이 자체는 모른다 — 실엔진 교체 시에도 이 파일은 수정되지 않는다.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)

_STATUS_RANK = {"queued": 0, "processing": 1, "done": 2, "failed": 2}
_TERMINAL_STATUSES = frozenset({"done", "failed"})


def new_job_id() -> str:
    """"j_" + uuid4 hex 앞 12자 (§3 Response)."""
    return f"j_{uuid.uuid4().hex[:12]}"


@dataclass
class Job:
    job_id: str
    status: str = "queued"
    image_url: Optional[str] = None
    error_code: Optional[str] = None
    created_at: float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None

    @property
    def elapsed_ms(self) -> int:
        """POST 수리 시점부터 경과 ms. 종결 후에는 종결 시점 값으로 고정된다."""
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return int((end - self.created_at) * 1000)

    @property
    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES


class JobStore:
    """단일 이벤트 루프에서만 접근한다 (BackgroundTasks도 async이므로 동일 루프)."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def create(self) -> Job:
        job = Job(job_id=new_job_id())
        self._jobs[job.job_id] = job
        log.info("job %s created (queued)", job.job_id)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def mark_processing(self, job_id: str) -> None:
        self._transition(job_id, "processing")

    def finish(
        self,
        job_id: str,
        *,
        image_url: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> None:
        """done 또는 failed로 종결한다. image_url/error_code 중 하나만 채운다."""
        if error_code:
            self._transition(job_id, "failed", error_code=error_code)
        elif image_url:
            self._transition(job_id, "done", image_url=image_url)
        else:
            log.error("job %s finished with neither image_url nor error_code", job_id)
            self._transition(job_id, "failed", error_code="UNKNOWN")

    def _transition(
        self,
        job_id: str,
        status: str,
        *,
        image_url: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            log.warning("transition to %s for unknown job %s ignored", status, job_id)
            return

        if job.is_terminal:
            log.warning(
                "job %s already terminal (%s); ignoring transition to %s",
                job_id,
                job.status,
                status,
            )
            return

        if _STATUS_RANK[status] < _STATUS_RANK[job.status]:
            log.warning(
                "refusing status regression for job %s: %s -> %s",
                job_id,
                job.status,
                status,
            )
            return

        job.status = status
        job.image_url = image_url
        job.error_code = error_code
        if status in _TERMINAL_STATUSES:
            job.finished_at = time.monotonic()

        log.info(
            "job %s -> %s (elapsed_ms=%d, image_url=%s, error_code=%s)",
            job_id,
            status,
            job.elapsed_ms,
            image_url,
            error_code,
        )
