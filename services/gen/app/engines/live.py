"""라이브 생성 엔진 공통 골격 — R5 이관 (06 §11-2 · 05 부록 A-15 ①③④ · A-17 ①⑥ · A-18 ⑦).

한 번의 `generate()`가 하는 일, 순서 그대로:

    ref/사진 URL 다운로드 → on_processing() → prompts.assemble("pv1")
    → 프로바이더 **1콜(N=1)** → (network 오류이고 예산이 남으면) **순차 재시도 1회**
    → 스토리지 업로드 → EngineResult.ok(공개 URL)

`gemini_nb`·`gpt_image` 두 엔진이 이 골격을 공유하고, 다른 것은 클래스 속성 네 개
(`model_id`·`max_ref_images`·`provider_params`·`single_call_p50_seconds`)와 어댑터뿐이다.

---

**① N=2 병렬 first-ok는 만들지 않는다.** 06 §11-2가 지시하는 "R4 확정 정책(N=2 병렬
first-ok)"은 **낡았다**. A-16 ①이 `1-(1-p)^2`를 기각했다 — first-ok는 *빠른 것*을 고르지
*좋은 것*을 고르지 않으므로 **N은 품질을 1도 올리지 않는다**(품질은 여전히 단일 추출이다).
N이 막는 것은 하드 실패뿐인데, 라이브 엔진이 `gemini_nb`로 확정되면서(A-17 ①) 단건 p95
18.4s → 순차 2회 합 p95 31.1s로 **57s 안에 25.9s가 남는다.** 즉 병렬로 발사하고 첫 성공에
나머지를 취소하는 조각이 통째로 필요 없어졌다. 비용도 절반이다.

**② 타임아웃은 고정 슬롯이 아니라 동적 배분이다** (A-15 ①-2). 동결값이던
`다운로드 5 + 생성 40 + 업로드 5 + 여유 5`의 "생성 40s" 슬롯은 R6 24장 중 **1장(4%)**만
충족했다 — 세 세션이 연속으로 반박한 상수였다. 여기서는 매 단계 **남은 예산에서 다음
단계 몫을 빼서** 그때그때 정한다. 업로드 몫은 스토리지 구현이 스스로 선언한다
(`ResultStorage.upload_reserve_seconds`) — 로컬 디스크와 Supabase 왕복은 자릿수가 다르다.

**③ 재시도는 network 오류 1회뿐이고, 그것도 예산이 허락할 때만이다** (A-15 ③).
- `timeout` 재시도 **삭제**: 이미 타임아웃 예산을 다 쓴 시도다. 다시 걸면 벽시계만 2배가
  된다 (연구 러너도 같은 이유로 `_RETRYABLE = {"error"}`였다).
- `refused` 순화 변형 재시도 **삭제**: 누적 151시도에서 **0건**이라 발동 조건이 없고,
  순화 프롬프트를 지금 만들면 `pv2 = pv1` 동결(A-13 ⑦)을 스스로 깬다. 방어선은 엔진 안이
  아니라 **B의 폴백**이다 (00 §5 — POST 1회 재시도 후 데모 모드).
- 남은 `network`도 **고정 "1회"가 아니라 "잔여 예산 > 단건 p50일 때만 1회"**다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, ClassVar, Optional, Sequence

import httpx

from . import prompts
from .base import EngineRequest, EngineResult, GenerationEngine
from .providers.base import GenCall, GenOutcome, sniff_image_ext
from .storage import ResultStorage, StorageError, create_storage

log = logging.getLogger(__name__)

PROMPT_VERSION = "pv1"
"""**pv2 = pv1 (05 부록 A-13 ⑦).** 06 §11-2는 `assemble(..., "pv2")`라고 적었지만
`TEMPLATES`의 키는 `pv1`·`pv1c`·`pv1d`뿐이라 `"pv2"`를 넘기면 **런타임 KeyError로 터진다.**
`"pv2"` 별칭 키를 만들지 않는 이유: 별칭은 "pv2가 따로 있다"는 착각을 만들고, 다음 사람이
pv2만 고쳐 동결을 조용히 깬다. 실 API에 쓰인 버전은 동결이다 (research/README 승격 규율)."""

_SELF_CUT_MARGIN_SECONDS = 1.5
"""라우트의 하드 타임아웃(`asyncio.wait_for`, 57s)에 잘리기 **전에** 스스로 종결하기 위한 몫.
둘 다 결과는 `TIMEOUT`이라 계약상 차이는 없지만, 스스로 끊으면 단계별 소요(다운로드·API·
업로드)를 로그에 남길 수 있다 — 그 로그가 A-2의 I/O 실측 의무를 닫는 계측기다."""

_MIN_API_SECONDS = 5.0
"""이보다 짧은 예산으로는 호출을 **시작하지 않는다.** 단건 p50이 15s(gemini)~45s(gpt)이므로
5s는 성공 가능성이 사실상 0인데 과금은 발생한다. 어차피 결과가 `TIMEOUT`이라면 돈을 쓰지 않고
같은 곳에 도착하는 편이 낫다."""

_DOWNLOAD_CAP_SECONDS = 12.0
"""다운로드 단독 상한. 예산 전체를 다운로드가 먹어 생성이 시작조차 못 하는 것을 막는다.
Supabase 공개 URL에서 이미지 3장(사진 1 + ref 2)에 12s면 이미 회선이 망가진 상태다."""

_ADAPTER_GRACE_SECONDS = 0.5
"""어댑터가 자체 타임아웃을 못 지켜도 우리가 끊는다 (연구 러너의 `+5s`와 같은 장치, 값만
예산에 맞게 줄였다). 스토리지의 `upload_reserve_seconds`가 1.0 이상이라 이 유예가 업로드
몫을 침범하지 않는다."""

_MAX_IMAGE_BYTES = 25 * 1024 * 1024
"""입력 1장 상한. B가 보내는 것은 §7.1/§7.2로 정규화된 이미지이므로 이 선은 사고 방지용이다."""


class DownloadError(RuntimeError):
    """입력 URL을 가져오지 못했다. `timed_out`이면 TIMEOUT, 아니면 UPSTREAM_ERROR."""

    def __init__(self, message: str, *, timed_out: bool = False) -> None:
        super().__init__(message)
        self.timed_out = timed_out


class LiveEngine(GenerationEngine):
    """실제 이미지 생성 엔진의 공통 골격. 서브클래스는 아래 5개만 정의한다."""

    name: ClassVar[str] = "live"

    default_timeout_seconds: ClassVar[float] = 57.0
    """**55s → 57s** (A-15 ①-1). 계약은 60초 종결이고(00 §5) 라우트 종결에 ~3s면 충분하다.
    55s로 자르면 **성공했을 58~59s 생성을 우리 손으로 버린다** — R6 24장 중 6장이 그 구간이었다.
    `engines/base.py`의 기본값 55.0은 **건드리지 않는다**: 그 값은 mock을 포함한 모든 엔진의
    기본선이고, 여기서 클래스 속성으로 덮는 것만으로 충분하다."""

    model_id: ClassVar[str] = ""
    """models.yaml에 있던 문자열 그대로. 코드는 모델명을 해석하지 않는다 (06 §3)."""

    max_ref_images: ClassVar[int] = 2
    """A-17 ② — R7 동수(4/6 vs 4/6)에서 사전 확정 규칙(동수 -> ref2)을 적용해 **2**로 확정.
    00-common §7.1(스트랩 선명 컷 1장 필수)과 구현이 일치한다. B가 3장을 보내도 2장만 쓴다."""

    provider_params: ClassVar[dict] = {}
    """어댑터가 프로바이더 문법으로 번역한다. `max_ref_images`는 여기 넣지 않는다 —
    그건 우리 쪽 노브이고 어댑터에 그대로 흘리면 API가 400을 돌려준다."""

    single_call_p50_seconds: ClassVar[float] = 15.0
    """재시도 판정선 (A-15 ③(c) "잔여 예산 > 단건 p50"). 관측값에서 온다."""

    def __init__(self, *, static_dir: Path) -> None:
        # 둘 다 **기동 시점에** 만든다 — 키·설정이 틀렸으면 첫 사용자 요청이 아니라
        # 부팅에서 터져야 한다 (03 §7 "조용한 기본값 금지"와 같은 규율).
        self._storage: ResultStorage = create_storage(static_dir=static_dir)
        self._client = self._build_client()
        log.info(
            "%s ready: model_id=%s max_ref_images=%d params=%s storage=%s wrapper=%.0fs",
            self.name,
            self.model_id,
            self.max_ref_images,
            self.provider_params,
            self._storage.name,
            self.default_timeout_seconds,
        )

    # --- 서브클래스가 채우는 부분 -------------------------------------------

    def _build_client(self) -> Any:
        """어댑터 클라이언트를 만든다. **SDK import는 이 메서드 안에서** 한다."""
        raise NotImplementedError

    # --- 실행 ---------------------------------------------------------------

    async def generate(self, request: EngineRequest) -> EngineResult:
        deadline = time.monotonic() + self.default_timeout_seconds - _SELF_CUT_MARGIN_SECONDS
        reserve = self._storage.upload_reserve_seconds
        payload = request.payload
        job_id = request.job_id

        def remaining() -> float:
            return deadline - time.monotonic()

        # 1) 입력 다운로드 (사람 사진 1 + 상품 ref 최대 max_ref_images장, 동시).
        refs = payload.product.ref_image_urls[: self.max_ref_images]
        urls = [payload.user_photo_url, *refs]
        download_budget = min(_DOWNLOAD_CAP_SECONDS, remaining() - reserve - _MIN_API_SECONDS)
        if download_budget <= 0:
            log.warning("job %s: no budget left before download", job_id)
            return EngineResult.fail("TIMEOUT")

        started = time.monotonic()
        try:
            blobs = await _download_all(urls, timeout=download_budget)
        except DownloadError as exc:
            log.warning("job %s download failed: %s", job_id, exc)
            return EngineResult.fail("TIMEOUT" if exc.timed_out else "UPSTREAM_ERROR")
        download_ms = int((time.monotonic() - started) * 1000)

        # 2) 여기서부터가 "실제 작업" — job을 processing으로 올린다 (06 §11-2 순서).
        request.on_processing()

        # 3) 프롬프트 조립. product dict는 계약 필드명 그대로라 연구 코드가 무수정으로 돈다
        #    (06 §8 — products.yaml 항목 = payload.product.model_dump()).
        prompt_text = prompts.assemble(
            payload.product.model_dump(),
            payload.style_hints.model_dump() if payload.style_hints else None,
            PROMPT_VERSION,
        )

        # 4) 생성 — N=1 + (조건부) 순차 재시도 1회.
        outcome: Optional[GenOutcome] = None
        api_ms = 0
        attempts = 0
        for attempt in (1, 2):
            api_budget = remaining() - reserve
            if api_budget < _MIN_API_SECONDS:
                log.warning(
                    "job %s: %.1fs left before attempt %d - not starting a paid call",
                    job_id,
                    api_budget,
                    attempt,
                )
                if outcome is None:
                    return EngineResult.fail("TIMEOUT")
                break

            call = GenCall(
                prompt=prompt_text,
                user_photo=blobs[0],
                product_refs=blobs[1:],
                model_id=self.model_id,
                params=dict(self.provider_params),
                timeout_s=api_budget,
            )
            started = time.monotonic()
            outcome = await _call_with_hard_cut(self._client, call)
            api_ms += int((time.monotonic() - started) * 1000)
            attempts = attempt

            log.info(
                "job %s attempt %d: status=%s latency_ms=%d %s",
                job_id,
                attempt,
                outcome.status,
                outcome.latency_ms,
                outcome.error_message or "",
            )

            if outcome.status == "ok":
                break
            if attempt == 2 or not _is_network_error(outcome):
                break
            # A-15 ③(c): 고정 "1회"가 아니라 **예산이 허락할 때만** 1회.
            if remaining() - reserve <= self.single_call_p50_seconds:
                log.info(
                    "job %s: network error but only %.1fs left (< p50 %.1fs) - no retry",
                    job_id,
                    remaining() - reserve,
                    self.single_call_p50_seconds,
                )
                break
            log.info("job %s: network error - sequential retry", job_id)

        if outcome is None or outcome.status != "ok" or not outcome.image:
            return EngineResult.fail(_error_code_for(outcome))

        # 5) 업로드 → 공개 URL.
        if sniff_image_ext(outcome.image) != "jpg":
            # 계약 경로는 `/generated/{job_id}.jpg` 고정이다 (00 §6). 확장자를 바꾸지 않고
            # 사실만 남긴다 — 어댑터가 jpeg를 요청하므로 이게 뜨면 프로바이더 쪽이 변한 것이다.
            log.warning("job %s: provider returned non-jpeg bytes; storing as .jpg", job_id)

        started = time.monotonic()
        try:
            image_url = await asyncio.wait_for(
                self._storage.put_jpeg(job_id, outcome.image, base_url=request.base_url),
                timeout=max(remaining(), 1.0),
            )
        except asyncio.TimeoutError:
            log.warning("job %s: storage upload exceeded the remaining budget", job_id)
            return EngineResult.fail("TIMEOUT")
        except StorageError as exc:
            log.error("job %s: %s", job_id, exc)
            return EngineResult.fail("UPSTREAM_ERROR")
        upload_ms = int((time.monotonic() - started) * 1000)

        # ★ A-2 · A-13 ⑦의 "왕복 I/O 실측" 의무를 닫는 계측 한 줄. 05 부록에 옮겨 적을 값이다.
        log.info(
            "job %s done: engine=%s storage=%s attempts=%d "
            "download_ms=%d api_ms=%d upload_ms=%d io_ms=%d url=%s",
            job_id,
            self.name,
            self._storage.name,
            attempts,
            download_ms,
            api_ms,
            upload_ms,
            download_ms + upload_ms,
            image_url,
        )
        return EngineResult.ok(image_url)


# --- 보조 ------------------------------------------------------------------


def _error_code_for(outcome: Optional[GenOutcome]) -> str:
    """어댑터 status → 계약 error_code (06 §11-2).

    `refused`도 `UPSTREAM_ERROR`다. 계약의 `BAD_INPUT`은 **요청 검증 실패** 전용이고
    (main.py의 validation 핸들러), 정책 거부는 우리 입력이 문법적으로 틀렸다는 뜻이 아니다.
    B는 어차피 둘을 같은 방식으로 처리한다 — POST 1회 재시도 후 데모 모드 폴백 (00 §5).
    """
    if outcome is None:
        return "UNKNOWN"
    if outcome.status == "timeout":
        return "TIMEOUT"
    if outcome.status in ("refused", "error"):
        return "UPSTREAM_ERROR"
    return "UNKNOWN"


def _is_network_error(outcome: GenOutcome) -> bool:
    """재시도해도 되는 것은 **전송 실패**뿐이다 (A-15 ③(c)).

    판정을 예외 **클래스 이름**이 아니라 **HTTP 상태의 부재**로 한다: 어댑터가 상태 코드를
    뽑지 못했다는 것은 업스트림이 아예 답하지 않았다는 뜻이고, 그게 곧 전송 실패다.
    상태 코드가 붙은 실패(4xx·5xx·429)는 업스트림이 답한 것이므로 재시도 대상이 아니다 —
    특히 429를 즉시 다시 때리면 상황이 나빠지고, A-15 ③은 network만 남겼다.
    """
    if outcome.status != "error":
        return False
    meta = outcome.provider_meta or {}
    return meta.get("classified_from") == "exception" and meta.get("http_status") is None


async def _call_with_hard_cut(client: Any, call: GenCall) -> GenOutcome:
    """어댑터가 자체 타임아웃을 못 지켜도 여기서 끊는다. 예외는 데이터로 정규화한다."""
    started = time.perf_counter()

    def _ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    try:
        return await asyncio.wait_for(
            client.generate(call), timeout=call.timeout_s + _ADAPTER_GRACE_SECONDS
        )
    except asyncio.TimeoutError:
        return GenOutcome.failed("timeout", _ms(), "hard timeout in engine wrapper")
    except Exception as exc:  # noqa: BLE001 — 어떤 SDK 예외든 계약 종결로 바꾼다
        return GenOutcome.failed("error", _ms(), f"{type(exc).__name__}: {exc}")


async def _download_all(urls: Sequence[str], *, timeout: float) -> list[bytes]:
    """입력 이미지를 동시에 받아 **URL 순서 그대로** 돌려준다.

    순서가 계약이다 — `blobs[0]`이 사람 사진이고 나머지가 상품 ref다 (06 §4 "역할 명명").
    클라이언트를 job마다 새로 만든다: 커넥션 재사용 이득(~100ms)보다, 수명 관리를 위해
    `main.py`에 lifespan 훅을 추가해야 하는 쪽이 비싸다 (main.py는 diff 0줄이 규율이다).
    """

    async def fetch(client: httpx.AsyncClient, url: str) -> bytes:
        response = await client.get(url)
        response.raise_for_status()
        data = response.content
        if not data:
            raise DownloadError(f"empty body from {url}")
        if len(data) > _MAX_IMAGE_BYTES:
            raise DownloadError(f"{url} is {len(data)} bytes (> {_MAX_IMAGE_BYTES})")
        return data

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            return await asyncio.wait_for(
                asyncio.gather(*(fetch(client, url) for url in urls)), timeout=timeout
            )
    except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
        raise DownloadError(f"download timed out after {timeout:.1f}s", timed_out=True) from exc
    except DownloadError:
        raise
    except httpx.HTTPStatusError as exc:
        raise DownloadError(f"HTTP {exc.response.status_code} from {exc.request.url}") from exc
    except httpx.HTTPError as exc:
        raise DownloadError(f"{type(exc).__name__}: {exc}") from exc
