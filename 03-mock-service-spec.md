# Mock Generation Service — 구현 기획서 (v2)

> **이 문서의 용도**: AI 코딩 세션에 그대로 투입하는 자기완결 스펙. `00-common.md` §5(v2) 계약의 파생 문서. 충돌 시 공통 문서 우선.
>
> **v2 변경점 (이미 v1으로 구현을 시작했다면 이 diff만 반영)**
> - `product.id` = MCM SKU 사용, `product.pattern` **삭제**
> - `product.material`(소재 원문), `product.color_hardware`(컬러&하드웨어 원문), **`product.wear_position`(필수)** 추가
> - `style_hints.tpo` → `style_hints.purpose` 로 키 이름 변경
> - 검증 규칙에 `wear_position` 존재 확인 추가

---

## 0. 목적과 위치

- Dev B가 전체 플로우를 즉시 개발할 수 있도록, **실제 생성 없이** Generation API 계약대로 동작하는 서비스를 최우선 배포.
- **버리는 코드가 아니다** — 실엔진은 엔진 모듈만 교체해 들어온다. API 레이어는 실서비스 품질, 가짜는 엔진 구현체 하나로 격리.
- 위치: `services/gen/` (레포 없으면 단독 폴더 후 이동). 배포: Railway, `GEN_BASE_URL` 불변.

## 1. 기술 스택 (고정)

| 항목 | 값 |
|---|---|
| 런타임 / 프레임워크 | Python 3.11+, FastAPI + uvicorn |
| 의존성 | `fastapi`, `uvicorn[standard]`, `pydantic` — **추가 금지** |
| Job 저장 / 비동기 | 인메모리 dict / `BackgroundTasks` (Celery·Redis 금지) |

## 2. 절대 규칙 (위반 시 B의 코드가 깨진다)

1. §3~4의 **경로·상태 코드·JSON 필드명·enum 값을 한 글자도 바꾸지 않는다.** 필드 추가도 금지.
2. `POST /generate` 성공 = **202** (200 아님).
3. `GET /generate/{job_id}` 응답에 `status`, `image_url`, `error_code`, `elapsed_ms` **네 키 항상 포함** (없으면 `null`, 생략 금지).
4. 이미지는 **URL로만**. base64 수수 금지.
5. 모든 job은 **60초 내 done/failed 종결** (§5 `slow` 시나리오만 예외 — B 타임아웃 테스트 전용).
6. 인증: `/health` 제외 전 엔드포인트 `X-API-Key` == env `API_KEY`, 불일치 시 **401** `{"error_code":"UNAUTHORIZED","message":"..."}`.
7. CORS 미들웨어 금지(B는 서버사이드 프록시), websocket·DB·Supabase·외부 API 호출 금지.

## 3. API 계약 — POST /generate (v2)

### Request

```jsonc
// Headers: X-API-Key: {API_KEY}, Content-Type: application/json
{
  "user_photo_url": "https://example.com/photo.jpg",       // 필수, http(s)
  "product": {
    "id": "MMRGATA04CO001",                                // 필수 (MCM SKU)
    "name": "나파 가죽 트림 비세토스 모노그램 캔버스 Aren 미디엄 크로스바디 백",
    "category": "crossbody",
    "material": "비세토스 모노그램 캔버스, 나파 가죽 트림, 코튼 트윌 안감",
    "color_hardware": "꼬냑, 24K 골드 도금 브라스 플레이트",
    "wear_position": "cross",                              // 필수: hand|shoulder|cross|back|neck
    "ref_image_urls": ["https://example.com/p1.jpg"]       // 필수, 1개 이상 http(s)
  },
  "style_hints": {                                          // 선택 (전체/부분 생략 허용)
    "city": "milano", "purpose": "daily", "lighting": "natural"   // 또는 null
  }
}
```

### 검증 규칙

- `user_photo_url` 누락/URL 아님 → **400** `{"error_code":"BAD_INPUT","message":"<필드·사유>"}`
- `product.id` 누락, `product.wear_position` 누락, `ref_image_urls` 비었거나 URL 아님 → 400 동일
- URL 값에 `data:` 스킴 포함 → 400
- `wear_position`·`style_hints`의 **값 자체는 enum 검증하지 않는다** (미정의 slug 허용 — 유연성 유지)

### Response — `202 { "job_id": "j_a1b2c3d4e5f6" }` ("j_" + uuid4 hex 12자)

## 4. API 계약 — GET /generate/{job_id}, /health

```jsonc
// 200 — 네 키 항상 포함
{ "status": "queued"|"processing"|"done"|"failed",
  "image_url": null,          // done일 때만 URL
  "error_code": null,         // failed일 때만 BAD_INPUT|UPSTREAM_ERROR|TIMEOUT|UNKNOWN
  "elapsed_ms": 5231 }        // POST 수리 시점부터 경과 ms

// 없는 job_id → 404 { "error_code": "UNKNOWN", "message": "job not found" }
// GET /health → 200 "ok" (인증 불필요)
```

상태 전이: `queued`(~1초) → `processing` → `done`/`failed`. 역행 금지.

## 5. Mock 동작 사양

### 기본 (성공)

POST 수리 → `queued` → 1초 후 `processing` → env `MOCK_DELAY_SECONDS`(기본 5) 대기 → `done`.
`image_url`은 **서비스가 직접 서빙하는 정적 샘플의 절대 URL** (`/static` 마운트, env `PUBLIC_BASE_URL` 있으면 사용, 없으면 요청 base URL). **외부 스토리지 의존 금지** — B 인프라와 무관하게 단독 동작.

### 실패/지연 시뮬레이션 — 요청 헤더 `X-Mock-Scenario`

| 값 | 동작 |
|---|---|
| `success` (기본) | 기본 시나리오 |
| `fail` | 정상 지연 후 `failed` + `UPSTREAM_ERROR` |
| `timeout` | 정상 지연 후 `failed` + `TIMEOUT` |
| `slow` | `processing`을 env `MOCK_SLOW_SECONDS`(기본 75)초 유지 후 `done` — B의 60초 컷 검증 전용 |

미정의 값 = `success`. 실엔진 교체 후 이 헤더는 무시(에러 아님).

### 샘플 이미지

`static/samples/sample_1.jpg` 최소 1장 커밋. **세로 3:4** 비율, 인물+가방이면 이상적(임시 아무 이미지 가능 — 베이크오프 산출물로 교체 예정). 파일 부재 시 명확한 메시지와 함께 기동 실패 (조용한 placeholder 생성 금지).

## 6. 코드 구조 — 엔진 추상화 (이 문서에서 구조적으로 가장 중요)

```
services/gen/
├── app/
│   ├── main.py            # 라우트만 (엔진 내용 모름)
│   ├── schemas.py         # §3~4 pydantic 모델
│   ├── auth.py            # X-API-Key dependency
│   ├── jobs.py            # 인메모리 store + 상태 전이
│   └── engines/
│       ├── base.py        # GenerationEngine 인터페이스: async generate(request) -> EngineResult(image_url | error_code)
│       └── mock.py        # MockEngine (§5, 시나리오 처리 포함)
├── static/samples/
├── requirements.txt
└── README.md              # §8 curl 모음
```

env `ENGINE`(기본 `mock`)으로 구현체 주입. **실엔진 교체 = `engines/`에 클래스 추가 + env 변경**, 그 외 파일 무수정이 목표.

## 7. 환경변수 / 배포

| 변수 | 필수 | 기본 | 설명 |
|---|---|---|---|
| `API_KEY` | ✅ | — | 미설정 시 기동 실패 |
| `ENGINE` | | `mock` | 엔진 선택 |
| `MOCK_DELAY_SECONDS` / `MOCK_SLOW_SECONDS` | | `5` / `75` | 지연 조절 |
| `PUBLIC_BASE_URL` | | (요청 기반) | Railway 도메인 확정 후 설정 |

Railway: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, 헬스체크 `/health`, 슬립 없는 설정.

## 8. 완료 기준 (전부 curl 검증, README 수록)

- [ ] `/health` 200 (무인증) / 키 없이 POST → 401
- [ ] 정상 POST(v2 payload) → 202 / 즉시 GET → queued|processing / ~6초 후 → done + image_url 브라우저 열림
- [ ] `user_photo_url` 누락 → 400 / `wear_position` 누락 → 400 / `data:` URL → 400
- [ ] `X-Mock-Scenario: fail`→UPSTREAM_ERROR, `timeout`→TIMEOUT, `slow`→60초 시점 processing
- [ ] 없는 job_id → 404 UNKNOWN / GET 4키 항상 존재
- [ ] Railway 배포, 재기동 후 정상 (job 소실 허용)

## 9. B에게 전달 (배포 직후)

`GEN_BASE_URL`, `GEN_API_KEY`, curl 모음, `X-Mock-Scenario`·`MOCK_DELAY_SECONDS` 사용법.
