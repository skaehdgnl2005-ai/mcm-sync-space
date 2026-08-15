# services/gen — Generation Service (Dev A)

MCM Sync-Space의 생성 서비스. **(사람 사진 + 확정된 상품 이미지) → 화보** 변환 한 가지만 담당한다.

- 계약: `00-common.md` §5 (단일 소스) / 구현 스펙: `03-mock-service-spec.md`
- 현재 엔진: **`mock`** — 실제 이미지 생성 없이 계약대로만 동작한다. 5초 지연 후 이 서비스가 서빙하는 샘플 이미지 URL을 반환한다.
- 호출 방향은 B → A 단방향. 브라우저에서 직접 호출하지 않고 Next.js API 라우트가 서버사이드 프록시한다.
- `GEN_BASE_URL`은 실엔진 교체 후에도 **불변**이다. B는 이 mock 기준으로 전체 플로우를 끝까지 연결하면 된다.

---

## 1. API 요약

| 메서드 | 경로 | 인증 | 응답 |
|---|---|---|---|
| GET | `/health` | 불필요 | `200` body `ok` |
| POST | `/generate` | `X-API-Key` | **`202`** `{"job_id":"j_…"}` |
| GET | `/generate/{job_id}` | `X-API-Key` | `200` `{status, image_url, error_code, elapsed_ms}` |
| GET | `/static/samples/…` | 불필요 | 샘플 이미지 (mock 결과물) |

- `status`: `queued` → `processing` → `done` \| `failed`. 역행 없음.
- GET 응답은 **네 키를 항상 포함**한다. 해당 없으면 `null`.
- `error_code`: `BAD_INPUT` \| `UPSTREAM_ERROR` \| `TIMEOUT` \| `UNKNOWN` (실패 시에만, 그 외 `null`)
- `elapsed_ms`: POST 수리 시점부터 경과 ms. `done`/`failed` 이후에는 **종결 시점 값으로 고정**된다.
- 에러 바디는 전부 `{"error_code": "...", "message": "..."}` 형태. 401은 `UNAUTHORIZED`, 없는 job은 404 `UNKNOWN`.
- 이미지는 항상 URL. `data:` (base64) 값은 400으로 거절한다.

요청 예시 (계약 v2, 전체 필드):

```json
{
  "user_photo_url": "https://example.com/photo.jpg",
  "product": {
    "id": "MMRGATA04CO001",
    "name": "나파 가죽 트림 비세토스 모노그램 캔버스 Aren 미디엄 크로스바디 백",
    "category": "crossbody",
    "material": "비세토스 모노그램 캔버스, 나파 가죽 트림, 코튼 트윌 안감",
    "color_hardware": "꼬냑, 24K 골드 도금 브라스 플레이트",
    "wear_position": "cross",
    "ref_image_urls": ["https://example.com/p1.jpg"]
  },
  "style_hints": { "city": "milano", "purpose": "daily", "lighting": "natural" }
}
```

- `product.id`는 **MCM SKU**, `material` / `color_hardware`는 DB 원문 텍스트다 (형식 검증 없음).
- 필수는 `user_photo_url`, `product.id`, `product.wear_position`, `product.ref_image_urls`(1개 이상) 넷이다.
- `wear_position`(예: `hand`·`shoulder`·`cross`·`back`·`neck`)과 `style_hints`의 **값 자체는 enum 검증하지 않는다** — 미정의 값도 통과한다.
- `style_hints`는 전체 생략·부분 생략·`null` 전부 허용된다.
- 알 수 없는 키(v1의 `pattern`, `style_hints.tpo` 등)는 에러 없이 무시된다. 하위호환 shim은 없다.

## 2. 로컬 실행

```bash
cd services/gen
python -m venv .venv && . .venv/bin/activate      # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

export API_KEY=dev-local-key                       # 필수. 미설정 시 기동 실패
uvicorn app.main:app --reload --port 8000
```

PowerShell:

```powershell
$env:API_KEY='dev-local-key'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

OpenAPI 문서는 `http://localhost:8000/docs`.

## 3. curl 스모크 테스트 (= 03-mock-service-spec.md §8 완료 기준)

```bash
GEN=http://localhost:8000          # 배포 후에는 Railway 도메인
KEY=dev-local-key                  # = GEN_API_KEY
BODY='{"user_photo_url":"https://example.com/photo.jpg","product":{"id":"MMRGATA04CO001","category":"crossbody","material":"비세토스 모노그램 캔버스, 나파 가죽 트림","color_hardware":"꼬냑, 24K 골드 도금 브라스 플레이트","wear_position":"cross","ref_image_urls":["https://example.com/p1.jpg"]},"style_hints":{"city":"milano","purpose":"daily","lighting":"natural"}}'

# 1) health — 키 없이 200 "ok"
curl -si $GEN/health | head -1

# 2) 인증 — 키 없이 POST → 401 UNAUTHORIZED
curl -s -w '\nHTTP %{http_code}\n' -X POST $GEN/generate \
  -H 'Content-Type: application/json' -d "$BODY"

# 3) 정상 POST → 202 + job_id
JOB=$(curl -s -X POST $GEN/generate \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d "$BODY" \
  | sed -E 's/.*"job_id":"([^"]+)".*/\1/')
echo "job=$JOB"

# 4) 즉시 GET → queued 또는 processing
curl -s $GEN/generate/$JOB -H "X-API-Key: $KEY"

# 5) 약 6초 후 GET → done + image_url (브라우저에서 열림)
sleep 6 && curl -s $GEN/generate/$JOB -H "X-API-Key: $KEY"

# 6) 400 BAD_INPUT — user_photo_url 누락
curl -s -w '\nHTTP %{http_code}\n' -X POST $GEN/generate \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"product":{"id":"MMRGATA04CO001","wear_position":"cross","ref_image_urls":["https://example.com/p1.jpg"]}}'

# 7) 400 BAD_INPUT — data: base64 URL 금지
curl -s -w '\nHTTP %{http_code}\n' -X POST $GEN/generate \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"user_photo_url":"data:image/jpeg;base64,/9j/4AAQ","product":{"id":"MMRGATA04CO001","wear_position":"cross","ref_image_urls":["https://example.com/p1.jpg"]}}'

# 7-1) 400 BAD_INPUT — wear_position 누락 (v2 필수 필드. 422가 아니라 400이어야 한다)
curl -s -w '\nHTTP %{http_code}\n' -X POST $GEN/generate \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"user_photo_url":"https://example.com/photo.jpg","product":{"id":"MMRGATA04CO001","ref_image_urls":["https://example.com/p1.jpg"]}}'

# 7-2) 202 — wear_position 미정의 값 + 알 수 없는 키(pattern, tpo)는 무시
curl -s -w '\nHTTP %{http_code}\n' -X POST $GEN/generate \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"user_photo_url":"https://example.com/photo.jpg","product":{"id":"MMRGATA04CO001","pattern":"visetos_cognac","wear_position":"elbow","ref_image_urls":["https://example.com/p1.jpg"]},"style_hints":{"tpo":"cafe"}}'

# 8) 실패 시나리오 — failed + UPSTREAM_ERROR
JOB=$(curl -s -X POST $GEN/generate -H "X-API-Key: $KEY" -H 'X-Mock-Scenario: fail' \
  -H 'Content-Type: application/json' -d "$BODY" | sed -E 's/.*"job_id":"([^"]+)".*/\1/')
sleep 7 && curl -s $GEN/generate/$JOB -H "X-API-Key: $KEY"

# 9) 타임아웃 시나리오 — failed + TIMEOUT
JOB=$(curl -s -X POST $GEN/generate -H "X-API-Key: $KEY" -H 'X-Mock-Scenario: timeout' \
  -H 'Content-Type: application/json' -d "$BODY" | sed -E 's/.*"job_id":"([^"]+)".*/\1/')
sleep 7 && curl -s $GEN/generate/$JOB -H "X-API-Key: $KEY"

# 10) slow 시나리오 — 60초 시점에도 processing (B의 클라이언트 타임아웃 검증)
JOB=$(curl -s -X POST $GEN/generate -H "X-API-Key: $KEY" -H 'X-Mock-Scenario: slow' \
  -H 'Content-Type: application/json' -d "$BODY" | sed -E 's/.*"job_id":"([^"]+)".*/\1/')
sleep 60 && curl -s $GEN/generate/$JOB -H "X-API-Key: $KEY"

# 11) 없는 job_id → 404 UNKNOWN
curl -s -w '\nHTTP %{http_code}\n' $GEN/generate/j_000000000000 -H "X-API-Key: $KEY"
```

> **PowerShell에서 실행할 때**: 세션 시작 시 `$PSNativeCommandArgumentPassing='Standard'`를 먼저 실행해야
> 인라인 JSON이 `curl.exe`에 그대로 전달된다. 그 외에는 `curl.exe`로 위 명령을 그대로 쓰면 된다.

## 4. `X-Mock-Scenario` 헤더 (B의 실패 경로 개발용)

POST `/generate` 요청에 붙인다. 헤더가 없으면 `success`.

| 값 | 동작 |
|---|---|
| `success` | 1초 후 `processing` → `MOCK_DELAY_SECONDS`(기본 5) 후 `done` + `image_url` |
| `fail` | 동일 지연 후 `failed` + `UPSTREAM_ERROR` |
| `timeout` | 동일 지연 후 `failed` + `TIMEOUT` |
| `slow` | `processing`을 `MOCK_SLOW_SECONDS`(기본 75)초 유지 후 `done` — 60초 타임아웃 검증 전용 |

미정의 값은 `success`로 처리한다(에러 아님). 실엔진 교체 후 이 헤더는 조용히 무시된다.

## 5. 환경변수

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `API_KEY` | ✅ | — | 인바운드 인증 키. B의 `GEN_API_KEY`와 동일 값. **미설정 시 기동 실패** |
| `ENGINE` | | `mock` | 엔진 선택. 미등록 값이면 기동 실패 |
| `MOCK_DELAY_SECONDS` | | `5` | 성공 시나리오 지연. B의 로딩 연출 테스트용으로 조절 (변경 시 재기동 필요) |
| `MOCK_SLOW_SECONDS` | | `75` | `slow` 시나리오 지연 |
| `PUBLIC_BASE_URL` | | (요청 기반) | 샘플 이미지 절대 URL 조립용. Railway 도메인 확정 후 설정 권장 |
| `LOG_LEVEL` | | `INFO` | 로그 레벨 |

`.env.example` 참고. 로컬에서는 셸 환경변수로 넣는다(파일 자동 로딩 없음).

## 6. Railway 배포

1. 새 프로젝트 → 이 레포 연결 → Root Directory `services/gen`
2. Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Variables: `API_KEY` (필수), 필요 시 `MOCK_DELAY_SECONDS`
4. Healthcheck Path: `/health`
5. 도메인 발급 후 `PUBLIC_BASE_URL=https://<도메인>` 추가 → 재배포
   (설정하지 않아도 요청 base URL로 조립되므로 동작은 한다)
6. 슬립(콜드스타트) 없는 설정 유지 — 발표 중 첫 요청이 느려지면 안 된다.

재기동 시 진행 중 job은 소실된다(인메모리, 허용된 동작). 서비스 자체는 정상 기동한다.

## 7. B에게 전달할 것

1. `GEN_BASE_URL` = Railway 도메인 (실엔진 교체 후에도 불변)
2. `GEN_API_KEY` = `API_KEY`와 동일 값 (별도 채널로 공유)
3. 이 README의 §3 curl 모음
4. §4 시나리오 헤더 사용법 + `MOCK_DELAY_SECONDS` 조절 가능 안내

## 8. 구조와 실엔진 교체

```
services/gen/
├── app/
│   ├── main.py            # FastAPI 라우트. 엔진 내부를 모른다
│   ├── schemas.py         # 계약 pydantic 모델 (필드명 변경 금지)
│   ├── auth.py            # X-API-Key 의존성
│   ├── jobs.py            # 인메모리 job store + 상태 전이
│   └── engines/
│       ├── __init__.py    # ENGINE env → 구현체 레지스트리
│       ├── base.py        # GenerationEngine / EngineRequest / EngineResult
│       └── mock.py        # MockEngine (가짜인 부분은 전부 여기)
├── static/samples/        # mock이 반환하는 샘플 이미지 (세로 3:4)
├── requirements.txt
└── .env.example
```

실엔진 투입 절차 — **`engines/` 안에서만 끝난다**:

1. `engines/<name>.py`에 `GenerationEngine` 서브클래스를 만든다.
   `generate()`에서 upstream 이미지 API 호출 → 결과를 Supabase `assets/generated/{job_id}.jpg`로 업로드 →
   `EngineResult.ok(public_url)` 반환. 실패는 `EngineResult.fail("UPSTREAM_ERROR")` 등.
   작업 시작 시점에 `request.on_processing()`을 호출하면 job이 `processing`으로 전이된다.
2. `engines/__init__.py`의 `_BUILDERS`에 한 줄 등록한다.
3. `requirements.txt`에 필요한 SDK를 추가한다.
4. Railway env `ENGINE=<name>`으로 변경 후 재배포.

`main.py` / `schemas.py` / `jobs.py` / `auth.py`는 수정하지 않는다. 하드 타임아웃은 라우트 레이어가
`GenerationEngine.timeout_seconds()`(기본 55초)로 강제하므로, 60초 내 `done`/`failed` 종결은 자동으로 보장된다.

## 9. 샘플 이미지 교체

`static/samples/`의 파일이 mock의 결과 이미지다. 현재는 세로 3:4 플레이스홀더 1장이며,
베이크오프 산출물(실제 인물+가방 화보)로 교체하면 B의 결과 화면이 그대로 리얼해진다.
여러 장을 넣으면 `job_id` 해시로 순환 선택된다. **파일이 하나도 없으면 기동에 실패한다**(조용한 폴백 금지).
