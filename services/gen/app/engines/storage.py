"""결과 이미지 저장 심(seam) — 구현 2개 (R5 브리프 §3).

`SUPABASE_URL`·`SUPABASE_SERVICE_ROLE_KEY`는 00-common §10상 **"B가 세팅 후 전달"**이고
R5 착수 시점에 아직 없다 (05 부록 A-17 ⑥의 블로커). 그래서 저장을 **인터페이스로 갈라**
구현을 둘 둔다:

    STORAGE=local      static/generated/{job_id}.jpg 에 쓰고 base_url 기반 절대 URL 반환
    STORAGE=supabase   POST {SUPABASE_URL}/storage/v1/object/assets/generated/{job_id}.jpg

이유 셋:
  1. **키 없이 오늘 전 구간 E2E가 돈다** — 생성 → 저장 → URL 반환 → 폴링까지 진짜로 돈다.
  2. **끝내 키가 안 와도 데모가 산다** — `local`이 실제 폴백 카드가 된다.
     ⚠ 단, **Railway 디스크는 휘발성**이다. 재배포·재시작이면 결과가 날아가고 이미 B에게
     건넨 `image_url`이 404가 된다. **데모 한정 카드**이며 영구 저장은 Supabase가 정답이다.
  3. **키가 도착하면 바뀌는 게 env 한 줄뿐이다** — 코드 재배포 불요.
     롤백 규율(`ENGINE=mock` 원복)과 같은 모양이다.

의존: `supabase-py`를 쓰지 않는다 (06 §11-2). 다운로드용 httpx를 그대로 재사용해 의존을
하나 아낀다. httpx import는 모듈 최상단에 두어도 되는데, 이 모듈은 라이브 엔진에서만
도달하고 라이브 엔진 자체가 lazy import이기 때문이다 (`ENGINE=mock` 경로는 여기 안 온다).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

log = logging.getLogger(__name__)

BUCKET = "assets"
"""00-common §6 스토리지 규약. public read 버킷 1개."""

PREFIX = "generated"
"""〃 `/generated/{job_id}.jpg` = 생성 결과, 쓰기 주체는 A."""

_JPEG_MIME = "image/jpeg"

ENV_STORAGE = "STORAGE"
ENV_SUPABASE_URL = "SUPABASE_URL"
ENV_SUPABASE_KEY = "SUPABASE_SERVICE_ROLE_KEY"


class StorageError(RuntimeError):
    """업로드 실패. 라이브 엔진이 UPSTREAM_ERROR로 종결시킨다."""


@runtime_checkable
class ResultStorage(Protocol):
    """(job_id, jpeg bytes) → 공개 http(s) URL. 그게 전부다."""

    name: str

    upload_reserve_seconds: float
    """엔진이 **호출 예산에서 미리 떼어 두는** 업로드 몫 (05 부록 A-15 ①-2의 동적 배분).
    고정 상수 대신 구현체가 스스로 선언한다 — 로컬 디스크와 Supabase 왕복은 자릿수가 다르다."""

    async def put_jpeg(self, job_id: str, data: bytes, *, base_url: str) -> str: ...


class LocalStorage:
    """이 서비스가 `/static`으로 직접 서빙하는 파일에 쓴다. 외부 의존 0.

    mock이 `static/samples/`를 쓰는 것과 같은 구조이며, 결과물은 `static/generated/`로
    분리한다 — mock은 `samples/` 안의 파일을 순환 선택하므로 생성 결과가 거기 섞이면
    mock 폴백 화면에 남의 세션 사진이 뜬다.
    """

    name = "local"
    upload_reserve_seconds = 1.0

    def __init__(self, static_dir: Path, url_path: str = "/static/generated") -> None:
        self._dir = static_dir / PREFIX
        self._dir.mkdir(parents=True, exist_ok=True)
        self._url_path = "/" + url_path.strip("/")
        log.info(
            "LocalStorage ready: dir=%s url_path=%s "
            "(⚠ Railway 디스크는 휘발성 — 데모 한정)",
            self._dir,
            self._url_path,
        )

    async def put_jpeg(self, job_id: str, data: bytes, *, base_url: str) -> str:
        try:
            (self._dir / f"{job_id}.jpg").write_bytes(data)
        except OSError as exc:
            raise StorageError(f"local write failed: {exc}") from exc
        return f"{base_url.rstrip('/')}{self._url_path}/{job_id}.jpg"


class SupabaseStorage:
    """Storage REST 직접 호출 (06 §11-2). service role key는 서버에서만 쓰인다.

    업로드: `POST /storage/v1/object/{bucket}/{path}` — 같은 job_id로 두 번 쓰는 일은
    없지만(job_id는 uuid4) `x-upsert`를 켜 둔다. 409로 죽는 것보다 덮어쓰는 편이 낫다.
    공개 URL: `/storage/v1/object/public/{bucket}/{path}` — 버킷이 public read라는 전제이며
    (00 §6) 아니면 URL이 401을 돌려준다. **이 전제의 실검증은 키 도착 후 E2E ⑦의 몫이다.**
    """

    name = "supabase"
    upload_reserve_seconds = 6.0
    """**실측 후에도 6.0을 유지한다** (05 부록 A-19 ④).

    실측(2026-08-19, 880KB): 업로드 p50 **0.98s**(773·980·1608ms) · 오리진 다운로드 p50 0.63s.
    360B 프로브가 0.57s인 것에서 보듯 **거의 전부가 지연이고 대역폭 성분은 ~80ms**다.

    그런데 상수는 내리지 않는다. 이유 둘:
      1. **의미가 있는 여유는 열화 시의 여유다.** 이 엔진을 고른 이유 자체가 "발표장 공용
         Wi-Fi에서 회선 3배 열화"를 견디는 것이다(A-16 ④). 최악 관측 1.6s × 3.7배 = 6.0s이므로
         이 값이 정확히 그 설계 목표를 덮는다.
      2. **내려도 얻는 것이 없다.** 6.0s를 떼도 API 예산이 ~48.5s 남고 gemini p95는 18.4s다.
         구속하지 않는 상수를 n=3 한 세션으로 조정하는 것은 A-11·A-17 ③이 금지한 종류의 튜닝이다.

    실측 원천은 엔진 로그의 `upload_ms` 한 줄이다. 회선이 다른 환경(Railway)에서 다시 재려면
    같은 줄을 읽으면 된다."""

    def __init__(self, url: str, service_role_key: str, bucket: str = BUCKET) -> None:
        self._url = url.rstrip("/")
        self._key = service_role_key
        self._bucket = bucket
        log.info("SupabaseStorage ready: %s bucket=%s", self._url, self._bucket)

    def _object_path(self, job_id: str) -> str:
        return f"{self._bucket}/{PREFIX}/{job_id}.jpg"

    async def put_jpeg(self, job_id: str, data: bytes, *, base_url: str) -> str:
        endpoint = f"{self._url}/storage/v1/object/{self._object_path(job_id)}"
        headers = {
            "Authorization": f"Bearer {self._key}",
            "apikey": self._key,
            "Content-Type": _JPEG_MIME,
            "x-upsert": "true",
            "Cache-Control": "public, max-age=31536000, immutable",
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(endpoint, content=data, headers=headers)
        except httpx.HTTPError as exc:
            raise StorageError(f"supabase upload failed: {type(exc).__name__}: {exc}") from exc

        if response.status_code >= 400:
            # 본문을 남긴다 — 401(키)·404(버킷 없음)·413(용량)은 처방이 전부 다르다.
            raise StorageError(
                f"supabase upload HTTP {response.status_code}: {response.text[:300]}"
            )

        # A-2 의무(왕복 I/O 실측)의 원천. 05 부록에 옮겨 적을 값이다.
        log.info(
            "supabase upload ok: job=%s bytes=%d roundtrip_ms=%d",
            job_id,
            len(data),
            int((time.perf_counter() - started) * 1000),
        )
        return f"{self._url}/storage/v1/object/public/{self._object_path(job_id)}"


def create_storage(*, static_dir: Path) -> ResultStorage:
    """env `STORAGE`(기본 `local`) → 구현체. 조용한 폴백 금지 — 설정이 틀리면 기동을 세운다.

    특히 `STORAGE=supabase`인데 키가 없을 때 local로 조용히 내려가면, 배포된 서비스가
    **휘발성 디스크에 쓰면서 정상으로 보인다.** 그건 발표 당일에 발견하는 종류의 사고다.
    """
    choice = (os.getenv(ENV_STORAGE) or "local").strip().lower()

    if choice == "local":
        return LocalStorage(static_dir=static_dir)

    if choice == "supabase":
        url = (os.getenv(ENV_SUPABASE_URL) or "").strip()
        key = (os.getenv(ENV_SUPABASE_KEY) or "").strip()
        missing = [
            name
            for name, value in ((ENV_SUPABASE_URL, url), (ENV_SUPABASE_KEY, key))
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"{ENV_STORAGE}=supabase requires {' and '.join(missing)}. "
                "Set them (00-common §10 — B가 전달) or run with STORAGE=local."
            )
        return SupabaseStorage(url=url, service_role_key=key)

    raise RuntimeError(f"unknown {ENV_STORAGE}={choice!r}. Available: local, supabase")
