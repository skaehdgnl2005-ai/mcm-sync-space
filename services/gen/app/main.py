"""FastAPI 앱 — 라우트 레이어. 엔진 내부는 모른다 (03-mock-service-spec.md §6).

계약: 00-common.md §5 / 03-mock-service-spec.md §3~4
  POST /generate            → 202 {"job_id"}
  GET  /generate/{job_id}   → 200 {"status","image_url","error_code","elapsed_ms"}
  GET  /health              → 200 "ok"  (인증 불필요)
CORS 미들웨어·websocket·DB·외부 API 호출 없음 (§2-7).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .auth import require_api_key
from .engines import EngineRequest, GenerationEngine, create_engine
from .jobs import JobStore
from .schemas import (
    ErrorResponse,
    GenerateAcceptedResponse,
    GenerateRequest,
    JobStatusResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("gen")

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
SAMPLES_DIR = STATIC_DIR / "samples"

if not SAMPLES_DIR.is_dir():
    raise RuntimeError(
        f"sample directory not found: {SAMPLES_DIR}. "
        "It must be committed to the repo (static/samples/sample_1.jpg, vertical 3:4)."
    )

_ERROR_CODE_BY_STATUS = {400: "BAD_INPUT", 401: "UNAUTHORIZED", 404: "UNKNOWN"}

_ERROR_RESPONSES: dict[int | str, dict] = {
    400: {"model": ErrorResponse, "description": "BAD_INPUT"},
    401: {"model": ErrorResponse, "description": "UNAUTHORIZED"},
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 둘 다 실패하면 기동을 중단시킨다 (§5, §7) — 조용한 기본값 금지.
    from .auth import load_api_key

    load_api_key()
    engine_name = os.getenv("ENGINE", "mock")
    engine = create_engine(engine_name, samples_dir=SAMPLES_DIR)

    app.state.engine = engine
    app.state.jobs = JobStore()

    log.info(
        "generation service v%s up: engine=%s, samples_dir=%s, PUBLIC_BASE_URL=%s",
        __version__,
        engine.name,
        SAMPLES_DIR,
        os.getenv("PUBLIC_BASE_URL") or "(request-based)",
    )
    yield
    log.info("generation service shutting down")


app = FastAPI(
    title="MCM Sync-Space — Generation Service",
    version=__version__,
    description="사람 사진 + 확정된 상품 이미지 → 화보. 00-common.md §5 계약 구현.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- 에러 응답 형태 통일 ------------------------------------------------------


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """pydantic 422를 계약상 400 BAD_INPUT으로 변환한다 (§3 검증 규칙)."""
    return JSONResponse(
        status_code=400,
        content={"error_code": "BAD_INPUT", "message": _first_validation_message(exc)},
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "error_code" in detail:
        content = detail
    else:
        content = {
            "error_code": _ERROR_CODE_BY_STATUS.get(exc.status_code, "UNKNOWN"),
            "message": str(detail),
        }
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error_code": "UNKNOWN", "message": "internal error"},
    )


def _first_validation_message(exc: RequestValidationError) -> str:
    """"<어느 필드가> <왜>" 형태로 압축한다."""
    errors = exc.errors()
    if not errors:
        return "invalid request body"

    first = errors[0]
    location = [str(part) for part in first.get("loc", ()) if part != "body"]
    field = ".".join(location) or "body"
    message = str(first.get("msg", "invalid value"))
    for prefix in ("Value error, ", "Assertion failed, "):
        if message.startswith(prefix):
            message = message[len(prefix) :]
    return f"{field}: {message}"


# --- 라우트 -----------------------------------------------------------------


@app.get("/health", response_class=PlainTextResponse, tags=["ops"])
async def health() -> str:
    """인증 불필요. Railway 헬스체크 경로."""
    return "ok"


@app.post(
    "/generate",
    status_code=202,
    response_model=GenerateAcceptedResponse,
    responses=_ERROR_RESPONSES,
    dependencies=[Depends(require_api_key)],
    tags=["generate"],
)
async def start_generation(
    payload: GenerateRequest,
    request: Request,
    background: BackgroundTasks,
    x_mock_scenario: Optional[str] = Header(default=None, alias="X-Mock-Scenario"),
) -> GenerateAcceptedResponse:
    jobs: JobStore = request.app.state.jobs
    engine: GenerationEngine = request.app.state.engine

    job = jobs.create()
    background.add_task(
        _run_job,
        engine=engine,
        jobs=jobs,
        job_id=job.job_id,
        payload=payload,
        base_url=_public_base_url(request),
        scenario=x_mock_scenario,
    )
    return GenerateAcceptedResponse(job_id=job.job_id)


@app.get(
    "/generate/{job_id}",
    response_model=JobStatusResponse,
    response_model_exclude_none=False,
    responses={**_ERROR_RESPONSES, 404: {"model": ErrorResponse, "description": "UNKNOWN"}},
    dependencies=[Depends(require_api_key)],
    tags=["generate"],
)
async def get_generation_status(job_id: str, request: Request) -> JobStatusResponse:
    jobs: JobStore = request.app.state.jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "UNKNOWN", "message": "job not found"},
        )

    return JobStatusResponse(
        status=job.status,
        image_url=job.image_url,
        error_code=job.error_code,
        elapsed_ms=job.elapsed_ms,
    )


# --- job 실행 ---------------------------------------------------------------


async def _run_job(
    *,
    engine: GenerationEngine,
    jobs: JobStore,
    job_id: str,
    payload: GenerateRequest,
    base_url: str,
    scenario: Optional[str],
) -> None:
    """백그라운드 실행. 어떤 경로로 끝나도 job은 done 또는 failed로 종결된다."""
    engine_request = EngineRequest(
        job_id=job_id,
        payload=payload,
        base_url=base_url,
        scenario=scenario or "",
        on_processing=lambda: jobs.mark_processing(job_id),
    )

    timeout = engine.timeout_seconds(engine_request)
    try:
        if timeout is None:
            result = await engine.generate(engine_request)
        else:
            result = await asyncio.wait_for(
                engine.generate(engine_request), timeout=timeout
            )
    except asyncio.TimeoutError:
        log.warning("job %s hit engine hard timeout (%ss)", job_id, timeout)
        jobs.finish(job_id, error_code="TIMEOUT")
        return
    except Exception:
        log.exception("job %s engine raised", job_id)
        jobs.finish(job_id, error_code="UNKNOWN")
        return

    jobs.finish(job_id, image_url=result.image_url, error_code=result.error_code)


def _public_base_url(request: Request) -> str:
    """PUBLIC_BASE_URL이 있으면 그것을, 없으면 요청의 base URL을 쓴다 (§5)."""
    configured = (os.getenv("PUBLIC_BASE_URL") or "").strip()
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/")
