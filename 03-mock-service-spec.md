# Mock Generation Service — 구현 기획서

> **이 문서의 용도**: AI 코딩 세션에 그대로 투입하는 자기완결 스펙.
> `00-common.md` §5 계약의 파생 문서이며, 여기서 추가 정의된 사항(§4의 404 응답, §5의 시나리오 헤더)은 공통 문서에 역반영한다. 충돌 시 공통 문서가 우선.

---

## 0. 목적과 위치

- Dev B가 전체 프로덕트 플로우를 즉시 개발할 수 있도록, **실제 이미지 생성 없이** Generation API 계약대로 동작하는 서비스를 최우선 배포한다.
- **이 코드는 버리는 코드가 아니다.** 이후 실엔진(gpt-image-2 / Nano Banana 등)은 이 서비스의 엔진 모듈만 교체해서 들어온다. 따라서 API 레이어는 처음부터 실서비스 품질로, 가짜인 부분은 엔진 구현체 하나로 격리한다.
- 위치: 모노레포 `services/gen/` (레포가 아직 없으면 단독 폴더로 만들고 나중에 이동).
- 배포: Railway. 배포 후 `GEN_BASE_URL`은 실엔진 교체 후에도 **불변**.

## 1. 기술 스택 (고정)

| 항목 | 값 |
|---|---|
| 런타임 | Python 3.11+ |
| 프레임워크 | FastAPI + uvicorn |
| 의존성 | `fastapi`, `uvicorn[standard]`, `pydantic` — **이 외 추가 금지** |
| Job 저장 | 인메모리 dict (프로세스 재시작 시 소실 허용) |
| 비동기 처리 | FastAPI `BackgroundTasks` (Celery/Redis/큐 금지) |

## 2. 절대 규칙 (위반 시 B의 코드가 깨진다)

1. 아래 §3~4의 **엔드포인트 경로, HTTP 상태 코드, JSON 필드명, enum 값을 한 글자도 바꾸지 않는다.** 필드 추가도 금지.
2. `POST /generate` 성공 응답은 **202** (200 아님).
3. `GET /generate/{job_id}` 응답에는 `status`, `image_url`, `error_code`, `elapsed_ms` **네 키를 항상 포함**한다 (해당 없으면 `null`, 키 생략 금지).
4. 이미지는 **URL로만** 주고받는다. base64를 받지도, 반환하지도 않는다.
5. 모든 job은 **60초 내에 `done` 또는 `failed`로 종결**된다 (§5의 `slow` 시나리오만 예외 — B의 타임아웃 테스트 전용).
6. 인증: `/health` 제외 전 엔드포인트에서 `X-API-Key` 헤더를 env `API_KEY`와 비교, 불일치/누락 시 **401** `{"error_code":"UNAUTHORIZED","message":"..."}`.
7. CORS 미들웨어 추가 금지 (B는 서버사이드 프록시로만 호출한다), websocket 금지, DB·Supabase·외부 API 호출 금지.

## 3. API 계약 — POST /generate

### Request

```jsonc
// Headers: X-API-Key: {API_KEY}, Content-Type: application/json
{
  "user_photo_url": "https://example.com/photo.jpg",   // 필수, http(s) URL
  "product": {                                          // 필수
    "id": "heritage-crossbody-cognac",                  // 필수
    "name": "MCM 헤리티지 비세토스 크로스백",
    "category": "crossbody",
    "pattern": "visetos_cognac",
    "material": "canvas_leather",
    "ref_image_urls": ["https://example.com/p1.jpg"]    // 필수, 1개 이상의 http(s) URL
  },
  "style_hints": {                                      // 선택 (없어도 정상 동작)
    "city": "milano",
    "tpo": "cafe",
    "lighting": "natural"                               // 또는 null
  }
}
```

### 검증 규칙

- `user_photo_url` 누락/URL 형식 아님 → **400** `{"error_code":"BAD_INPUT","message":"<어느 필드가 왜>"}`
- `product.id` 누락 또는 `product.ref_image_urls` 비어있음/URL 아님 → 400 동일
- URL 값에 `data:` 스킴(base64) 포함 → 400 BAD_INPUT
- `style_hints`는 전체 생략, 부분 생략, 미정의 slug 값 전부 허용 (검증하지 않고 통과)

### Response

```jsonc
// 202
{ "job_id": "j_a1b2c3d4e5f6" }   // "j_" + uuid4 hex 앞 12자
```

## 4. API 계약 — GET /generate/{job_id}, /health

```jsonc
// GET /generate/{job_id} → 200
{
  "status": "queued" | "processing" | "done" | "failed",
  "image_url": null,        // done일 때만 URL, 그 외 null
  "error_code": null,       // failed일 때만 "BAD_INPUT"|"UPSTREAM_ERROR"|"TIMEOUT"|"UNKNOWN"
  "elapsed_ms": 5231        // POST 수리 시점부터 경과 ms
}

// 존재하지 않는 job_id → 404
{ "error_code": "UNKNOWN", "message": "job not found" }

// GET /health → 200, body "ok" (인증 불필요)
```

상태 전이: `queued`(수리 직후 ~1초) → `processing` → `done` 또는 `failed`. 역행 금지.

## 5. Mock 동작 사양

### 기본 시나리오 (성공)

1. POST 수리 → job 생성(`queued`) → 202 반환.
2. 백그라운드에서 1초 후 `processing` 전환 → env `MOCK_DELAY_SECONDS`(기본 5)만큼 대기 → `done` 전환.
3. `image_url`은 **이 서비스가 직접 서빙하는 정적 샘플 이미지의 절대 URL**:
   - `static/samples/` 디렉토리를 `/static`에 마운트 (FastAPI `StaticFiles`).
   - 절대 URL은 env `PUBLIC_BASE_URL`이 있으면 그것을, 없으면 요청의 base URL을 사용해 조립.
   - 샘플이 여러 장이면 job_id 해시로 순환 선택 (선택 구현).
   - **Supabase 등 외부 스토리지에 의존하지 않는다** — B 인프라와 무관하게 단독 동작해야 함.

### 실패/지연 시뮬레이션 — 요청 헤더 `X-Mock-Scenario`

B가 재시도·폴백 경로(§공통 문서)를 개발·테스트하기 위한 장치. 헤더 없으면 `success`.

| 값 | 동작 |
|---|---|
| `success` | 기본 시나리오 |
| `fail` | 정상 지연 후 `failed`, `error_code: "UPSTREAM_ERROR"` |
| `timeout` | 정상 지연 후 `failed`, `error_code: "TIMEOUT"` |
| `slow` | `processing` 상태를 env `MOCK_SLOW_SECONDS`(기본 75)초 유지 후 `done` — **B의 60초 클라이언트 타임아웃 검증 전용** (§2-5의 유일한 예외) |

미정의 값은 `success`로 처리. 실엔진 교체 후 이 헤더는 무시된다(에러 아님).

### 샘플 이미지

- `static/samples/sample_1.jpg` 최소 1장을 레포에 커밋. **세로 3:4 비율**(B의 결과 화면 레이아웃 기준), 내용은 인물+가방 사진이 이상적이나 임시로 아무 이미지나 가능 — 추후 베이크오프 산출물로 교체.
- 기동 시 샘플 파일 부재면 명확한 에러 메시지와 함께 기동 실패 (조용한 placeholder 생성 금지).

## 6. 코드 구조 — 엔진 추상화 (실엔진 교체 대비)

```
services/gen/
├── app/
│   ├── main.py            # FastAPI 앱, 라우트만 (엔진 내용 모름)
│   ├── schemas.py         # §3~4 계약의 pydantic 모델
│   ├── auth.py            # X-API-Key dependency
│   ├── jobs.py            # 인메모리 job store + 상태 전이
│   └── engines/
│       ├── base.py        # GenerationEngine 인터페이스: async generate(request) -> EngineResult(image_url | error_code)
│       └── mock.py        # MockEngine (§5 구현, 시나리오 처리 포함)
├── static/samples/
├── requirements.txt
└── README.md              # 아래 §8의 curl 모음 포함
```

- 라우트 레이어는 env `ENGINE`(기본 `"mock"`)으로 엔진 구현체를 주입받는다. **실엔진 교체 = `engines/`에 클래스 추가 + env 변경**, 그 외 파일은 수정되지 않아야 한다. 이 격리가 이 기획서에서 구조적으로 가장 중요한 요구사항.

## 7. 환경변수 / 배포

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `API_KEY` | ✅ | — | 인바운드 인증 키. 미설정 시 기동 실패 |
| `ENGINE` | | `mock` | 엔진 선택 |
| `MOCK_DELAY_SECONDS` | | `5` | 성공 시나리오 지연 (B가 로딩 연출 테스트 시 조절) |
| `MOCK_SLOW_SECONDS` | | `75` | slow 시나리오 지연 |
| `PUBLIC_BASE_URL` | | (요청 기반) | 샘플 이미지 절대 URL 조립용, Railway 도메인 확정 후 설정 |

- Railway 배포: start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (Railway가 주입하는 `PORT` 사용), 헬스체크 경로 `/health`, 슬립 없는 설정.

## 8. 완료 기준 (전부 curl로 검증, README에 명령 수록)

- [ ] `GET /health` → 200 (키 없이)
- [ ] 키 없이 `POST /generate` → 401
- [ ] 정상 POST → 202 + job_id / 즉시 GET → `queued` 또는 `processing` / ~6초 후 GET → `done` + `image_url`이 브라우저에서 열림
- [ ] `user_photo_url` 누락 POST → 400 `BAD_INPUT` / `data:` base64 URL → 400
- [ ] `X-Mock-Scenario: fail` → `failed` + `UPSTREAM_ERROR` / `timeout` → `failed` + `TIMEOUT`
- [ ] `X-Mock-Scenario: slow` → 60초 시점에도 `processing`
- [ ] 없는 job_id GET → 404 `UNKNOWN`
- [ ] GET 응답에 네 키(`status/image_url/error_code/elapsed_ms`) 항상 존재
- [ ] Railway 배포 완료, 재기동 후에도 정상 (기존 job 소실은 허용)

## 9. B에게 전달할 것 (배포 직후)

1. `GEN_BASE_URL` (Railway 도메인)
2. `GEN_API_KEY` 값
3. README의 curl 스모크 테스트 모음
4. `X-Mock-Scenario` 헤더 사용법과 `MOCK_DELAY_SECONDS` 조절 가능 안내
