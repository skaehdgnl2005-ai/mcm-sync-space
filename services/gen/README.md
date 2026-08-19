# services/gen — Generation Service (Dev A)

MCM Sync-Space의 생성 서비스. **(사람 사진 + 확정된 상품 이미지) → 화보** 변환 한 가지만 담당한다.

- 계약: `00-common.md` §5 (단일 소스) / 구현 스펙: `03-mock-service-spec.md`
- **현재 엔진: `gemini_nb`** (실엔진, R5 이관 완료) — `gemini-3.1-flash-image`로 실제 화보를 생성해
  Supabase `assets/generated/`에 올린다.
  선택 근거는 지연이다: p95 **16.4s**로 60초 계약에 43.6s 여유가 남는다 (05 부록 A-17 ① · A-18).
- 엔진은 env 한 줄로 갈아 끼운다: `gemini_nb`(라이브) · `gpt_image`(폴백) · `mock`(최종 폴백 = 롤백 목적지).
- 호출 방향은 B → A 단방향. 브라우저에서 직접 호출하지 않고 Next.js API 라우트가 서버사이드 프록시한다.
- `GEN_BASE_URL`은 엔진 교체 후에도 **불변**이다. B는 mock 기준으로 연결해 둔 플로우를 그대로 쓰면 된다.

---

## 1. API 요약

| 메서드 | 경로 | 인증 | 응답 |
|---|---|---|---|
| GET | `/health` | 불필요 | `200` body `ok` |
| POST | `/generate` | `X-API-Key` | **`202`** `{"job_id":"j_…"}` |
| GET | `/generate/{job_id}` | `X-API-Key` | `200` `{status, image_url, error_code, elapsed_ms}` |
| GET | `/static/samples/…` | 불필요 | 샘플 이미지 (mock 결과물) |
| GET | `/static/generated/…` | 불필요 | 생성 결과 — **`STORAGE=local`일 때만**. 배포는 Supabase URL을 돌려준다 (§6.2) |

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
export ENGINE=mock                                 # 실엔진은 gemini_nb (§6)
uvicorn app.main:app --reload --port 8000
```

PowerShell:

```powershell
$env:API_KEY='dev-local-key'
$env:ENGINE='mock'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# 실엔진으로 띄울 때 (§6)
$env:ENGINE='gemini_nb'; $env:STORAGE='local'; $env:GOOGLE_API_KEY='<키>'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

`ENGINE=mock`은 **SDK가 하나도 설치돼 있지 않아도 부팅된다** — 엔진 등록이 lazy라서
`requirements.txt` 앞 3줄(fastapi·uvicorn·pydantic)만 있으면 계약 전체가 돈다.

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

**엔진별 적용 범위**: 1·2·3·4·6·7·7-1·7-2·11번은 **모든 엔진**에서 같은 결과다 (계약 레이어).
8·9·10번은 `X-Mock-Scenario`를 쓰므로 **`ENGINE=mock` 전용**이며, 실엔진에서는 헤더가 무시되어
평범한 생성 요청이 된다. 5번은 실엔진에서 `image_url`이 실제 생성물 URL이고 지연이 더 길다.

### 3.1 실엔진 E2E (`ENGINE=gemini_nb`)

위 `BODY`의 URL은 예시라 실엔진에서는 다운로드가 실패한다(→ `failed` + `UPSTREAM_ERROR`,
그 자체로 실패 경로 확인이 된다). **실제로 그림이 나오는지** 보려면 공개적으로 접근 가능한
사진·상품컷 URL을 넣는다. 로컬에서는 정적 서버 하나를 띄우는 것이 가장 간단하다:

```bash
# 별도 셸: 연구 자산을 http로 노출
python -m http.server 8001 --bind 127.0.0.1 --directory research/assets

# 생성 요청 → 폴링 → 브라우저로 image_url 열기
A=http://127.0.0.1:8001
curl -s -X POST $GEN/generate -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{
  "user_photo_url": "'$A'/selfies/s_casual.jpg",
  "product": {"id":"MMRGATA04CO001","name":"나파 가죽 트림 비세토스 모노그램 캔버스 Aren 미디엄 크로스바디 백",
              "category":"crossbody","material":"비세토스 모노그램 캔버스, 나파 가죽 트림, 코튼 트윌 안감",
              "color_hardware":"꼬냑, 24K 골드 도금 브라스 플레이트","wear_position":"cross",
              "ref_image_urls":["'$A'/products/MMRGATA04CO001/1.jpg","'$A'/products/MMRGATA04CO001/2.jpg"]},
  "style_hints": {"city":"milano","purpose":"daily","lighting":"natural"}}'
```

정상이면 ~15~25초 뒤 `done` + `image_url`이 나오고, 서버 로그에 단계별 소요가 한 줄로 남는다:

```
job j_… done: engine=gemini_nb storage=local attempts=1 download_ms=191 api_ms=21138 upload_ms=1 io_ms=192 url=…
```

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
| `ENGINE` | | `mock` | `mock` \| `gemini_nb` \| `gpt_image`. 미등록 값이면 기동 실패 |
| `GOOGLE_API_KEY` | `ENGINE=gemini_nb`일 때 ✅ | — | 미설정 시 **기동 실패** |
| `OPENAI_API_KEY` | `ENGINE=gpt_image`일 때 ✅ | — | 〃 |
| `STORAGE` | | `local` | `local` \| `supabase`. 결과 이미지 저장 위치 (§6) |
| `SUPABASE_URL` | `STORAGE=supabase`일 때 ✅ | — | 미설정 시 **기동 실패** (조용히 local로 안 내려간다) |
| `SUPABASE_SERVICE_ROLE_KEY` | 〃 | — | 〃. 서버에서만 쓰인다 — 절대 클라이언트로 내보내지 않는다 |
| `MOCK_DELAY_SECONDS` | | `5` | 성공 시나리오 지연. `ENGINE=mock` 전용 (변경 시 재기동 필요) |
| `MOCK_SLOW_SECONDS` | | `75` | `slow` 시나리오 지연. `ENGINE=mock` 전용 |
| `PUBLIC_BASE_URL` | | (요청 기반) | 결과·샘플 이미지 절대 URL 조립용. Railway 도메인 확정 후 설정 권장 |

`.env.example` 참고. 로컬에서는 셸 환경변수로 넣는다(파일 자동 로딩 없음).

**설정이 틀리면 조용히 넘어가지 않고 기동에서 죽는다.** 키가 빠진 채 배포되면 "동작하는 것처럼
보이는데 결과가 사라지는" 상태가 되고, 그건 발표 당일에 발견하는 종류의 사고다.

## 6. 엔진과 스토리지 (R5 이관 결과)

### 6.1 엔진 3종

| `ENGINE` | 모델 | 용도 | 60초 계약 여유 |
|---|---|---|---|
| **`gemini_nb`** | `gemini-3.1-flash-image` · 3:4 · 1K · ref 2장 | **라이브 기본값** | p95 16.4s → **43.6s** |
| `gpt_image` | `gpt-image-2` · mid · 1152×1536 · ref 2장 | 폴백 / OpenAI 크레딧 소진 | p95 58.3s → **1.7s** |
| `mock` | — | **최종 폴백 = 롤백 목적지.** 외부 의존 0 | 해당 없음 |

- **왜 gemini가 기본값인가**: 근거는 지연 하나다. 회선이 3배 열화돼도 계약 안이고, 실제 열화
  세션에서 gpt는 **60초 내 성공 0/23**이었다 (05 부록 A-13 ② · A-17 ①). 발표장 공용 Wi-Fi가
  정확히 그 시나리오다. 품질은 두 벤더가 사실상 무차별이다 (R8 셀 통과 7/9 vs 6/9).
- **생성 정책**: **N=1 + 순차 재시도 1회**다. 병렬 N호출은 만들지 않았다 — first-ok는 *빠른 것*을
  고르지 *좋은 것*을 고르지 않아 N이 품질을 올리지 않는다 (A-16 ①).
  재시도는 **전송 실패(network)일 때만**, 그것도 **잔여 예산 > 단건 p50**일 때 1회다.
  `timeout`·정책 거부(`refused`)는 **즉시 `failed`** — 방어선은 엔진 안이 아니라 B의 폴백이다.
- **타임아웃**: 래퍼 **57s**(고정 슬롯 없음). 다운로드·API·업로드 예산을 매 단계 남은 시간에서
  다시 계산한다. 60초 내 `done`/`failed` 종결은 라우트 레이어가 강제한다.
- `X-Mock-Scenario`는 실엔진에서 **조용히 무시**된다 (에러 아님).

### 6.2 스토리지 — `STORAGE=local` | `supabase`

생성 결과를 어디에 두고 어떤 URL로 돌려줄지만 정한다. 엔진 코드는 둘을 구분하지 않는다.

| | 저장 위치 | 반환 URL | 영속성 |
|---|---|---|---|
| `local` (기본) | `static/generated/{job_id}.jpg` | `{PUBLIC_BASE_URL}/static/generated/{job_id}.jpg` | **휘발성** |
| `supabase` | `assets` 버킷 `/generated/{job_id}.jpg` (00-common §6) | `…/storage/v1/object/public/assets/generated/{job_id}.jpg` | 영구 |

**배포에서는 `STORAGE=supabase`를 쓴다.** 코드 기본값이 `local`인 것은 키 없이도 로컬 개발과
mock 경로가 돌아야 하기 때문이지, `local`이 권장이라는 뜻이 아니다.

```powershell
# Railway Variables (재배포 불요, restart만)
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service role key>
STORAGE=supabase
```

> ⚠ **`STORAGE=local`은 데모 한정 폴백 카드다.** **Railway 디스크는 휘발성**이라 재배포·재시작에
> 결과가 전부 사라진다. 이미 B에게 건넨 `image_url`이 그 순간 404가 된다.
> Supabase가 죽었을 때만 쓰고, 그때도 "이 세션 동안만 산다"는 것을 알고 써야 한다.

**검증 완료 (2026-08-19, 05 부록 A-19 ④):**

| 확인 | 결과 |
|---|---|
| `assets` 버킷 public read | ✅ 반환 URL이 **인증 헤더 없이** 200 · `image/jpeg` · 바이트 동일 |
| E2E 1건 | ✅ `done` **19.2s** (생성 18.1s + I/O 1.1s) |
| **왕복 I/O 실측** | **≈1.6s** — 업로드 p50 **0.98s**(880KB) + 오리진 다운로드 p50 **0.63s** |
| CDN 캐시 히트 | 90~120ms (`cache-control: public, max-age=31536000, immutable`) |

> 360B 프로브도 오리진 0.57s다 — **왕복 시간은 거의 전부 지연이고 대역폭 성분은 ~80ms**다.
> 이미지를 줄여도 I/O는 안 준다. 57s 예산에서 스토리지 몫으로 떼는 `upload_reserve_seconds = 6.0`은
> 최악 관측(1.6s)의 3.7배이며, "회선 3배 열화까지 계약 충족"이라는 설계 목표를 덮는 값이다.

측정을 다시 하려면 로그 한 줄을 읽으면 된다 (환경이 바뀌면 Railway에서 다시 잰다):

```
job j_658a035b380d done: engine=gemini_nb storage=supabase attempts=1
                   download_ms=376 api_ms=18080 upload_ms=705 io_ms=1081
```

## 7. Railway 배포

1. 새 프로젝트 → 이 레포 연결 → Root Directory `services/gen`
2. Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
3. Variables:
   ```
   API_KEY=<GEN_API_KEY와 동일 값>     # 필수
   ENGINE=gemini_nb                    # 라이브. 롤백은 이 줄을 mock으로
   GOOGLE_API_KEY=<키>                 # ENGINE=gemini_nb의 필수 짝
   STORAGE=supabase                    # + SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY (§6.2)
   SUPABASE_URL=https://<project>.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=<service role key>
   ```
   폴백 전환을 대비해 `OPENAI_API_KEY`도 미리 넣어 두면 `ENGINE=gpt_image` 한 줄로 갈아탄다.
4. Healthcheck Path: `/health`
5. 도메인 발급 후 `PUBLIC_BASE_URL=https://<도메인>` 추가 → 재배포
   (설정하지 않아도 요청 base URL로 조립되므로 동작은 한다.
   단 **`STORAGE=local`이면 이 값이 곧 B가 받는 `image_url`의 호스트**이므로 설정해 두는 편이 안전하다)
6. 슬립(콜드스타트) 없는 설정 유지 — 발표 중 첫 요청이 느려지면 안 된다.

재기동 시 진행 중 job은 소실된다(인메모리, 허용된 동작). 서비스 자체는 정상 기동한다.
**`STORAGE=local`이면 재기동에 지난 결과 이미지까지 소실된다** (§6.2의 휘발성 경고).

### 롤백 (연습 완료)

문제가 생기면 **env `ENGINE=mock` 원복 하나**로 끝난다. 코드 재배포도, `GEN_BASE_URL` 변경도,
B쪽 수정도 없다. 중간 단계로 `ENGINE=gpt_image`(벤더 교체)를 먼저 시도할 수 있다.

```
ENGINE=gemini_nb  →  ENGINE=gpt_image  →  ENGINE=mock
   라이브              벤더 폴백              최종 폴백 (항상 성공)
```

## 8. B에게 전달할 것

1. `GEN_BASE_URL` = Railway 도메인 (엔진 교체 후에도 불변)
2. `GEN_API_KEY` = `API_KEY`와 동일 값 (별도 채널로 공유)
3. 이 README의 §3 curl 모음 (+ §3.1 실엔진 E2E)
4. §4 시나리오 헤더 사용법 + `MOCK_DELAY_SECONDS` 조절 가능 안내
5. **결과 이미지는 Supabase `assets/generated/{job_id}.jpg`로 간다** — `image_url`은
   `{SUPABASE_URL}/storage/v1/object/public/assets/generated/{job_id}.jpg` 형태이고 인증 없이 열린다.
   `cache-control: immutable`이라 재방문은 CDN 캐시로 ~100ms다 (§6.2)
6. **실엔진 지연 안내**: 성공 시 대략 15~25초다 (mock 5초와 다르다). 60초 폴링 규약은 그대로이고,
   실패는 `UPSTREAM_ERROR`/`TIMEOUT`으로 오므로 B쪽 폴백 경로는 이미 붙여 둔 것이 그대로 쓰인다

## 9. 구조와 엔진 교체

```
services/gen/
├── app/
│   ├── main.py            # FastAPI 라우트. 엔진 내부를 모른다
│   ├── schemas.py         # 계약 pydantic 모델 (필드명 변경 금지)
│   ├── auth.py            # X-API-Key 의존성
│   ├── jobs.py            # 인메모리 job store + 상태 전이
│   └── engines/
│       ├── __init__.py    # ENGINE env → 구현체 레지스트리 (등록은 lazy)
│       ├── base.py        # GenerationEngine / EngineRequest / EngineResult
│       ├── mock.py        # MockEngine (가짜인 부분은 전부 여기)
│       ├── live.py        # 라이브 엔진 공통 골격 (다운로드·예산·재시도·업로드)
│       ├── gemini_nb.py   # 라이브   — 설정만 담긴 얇은 서브클래스
│       ├── gpt_image.py   # 폴백     — 〃
│       ├── prompts.py     # ★ research/genlab/prompts.py 복사본 (동결)
│       ├── storage.py     # ResultStorage / LocalStorage / SupabaseStorage
│       └── providers/     # ★ research/genlab/providers/ 복사본
│           ├── base.py            # GenCall / GenOutcome (stdlib만)
│           ├── google_genai.py    # gemini 어댑터
│           └── openai_images.py   # openai 어댑터
├── static/samples/        # mock이 반환하는 샘플 이미지 (세로 3:4)
├── static/generated/      # STORAGE=local 산출물 (런타임 생성, 미추적, 휘발성)
├── requirements.txt
└── .env.example
```

**★ 표시 파일은 `research/genlab/`에서 복사-이식한 것이다** (R5 · 06 §11-1). 원본과 프롬프트
문자열이 달라지면 연구 결과가 실엔진을 설명하지 못하므로 **동결**이며, 개선은 원본 쪽 규율
(`TEMPLATES`는 추가만, 수정 금지)을 그대로 따른다. `services/gen`이 `research/`를 import하지
않는 이유는 Railway가 Root=`services/gen`으로 빌드해 **research/가 빌드 컨텍스트 밖**이기 때문이다.

엔진 추가 절차 — **`engines/` 안에서만 끝난다**:

1. `engines/<name>.py`에 `GenerationEngine`(또는 `live.LiveEngine`) 서브클래스를 만든다.
   `generate()`에서 upstream 이미지 API 호출 → 결과를 스토리지에 업로드 →
   `EngineResult.ok(public_url)` 반환. 실패는 `EngineResult.fail("UPSTREAM_ERROR")` 등.
   작업 시작 시점에 `request.on_processing()`을 호출하면 job이 `processing`으로 전이된다.
2. `engines/__init__.py`의 `_BUILDERS`에 한 줄 등록한다. **SDK import는 빌더 함수 안에서** 한다 —
   `ENGINE=mock`은 SDK가 하나도 설치돼 있지 않아도 부팅돼야 한다(검증됨: 06 §13).
3. `requirements.txt`에 필요한 SDK를 추가한다.
4. Railway env `ENGINE=<name>`으로 변경 후 재배포.

`main.py` / `schemas.py` / `jobs.py` / `auth.py`는 수정하지 않는다 — R5 이관에서도 **diff 0줄**이었다.
하드 타임아웃은 라우트 레이어가 `GenerationEngine.timeout_seconds()`로 강제하므로(기본 55초,
라이브 엔진은 **57초**로 덮는다), 60초 내 `done`/`failed` 종결은 자동으로 보장된다.

## 10. 샘플 이미지 교체

`static/samples/`의 파일이 mock의 결과 이미지다. 현재는 세로 3:4 플레이스홀더 1장이며,
베이크오프 산출물(실제 인물+가방 화보)로 교체하면 B의 결과 화면이 그대로 리얼해진다.
여러 장을 넣으면 `job_id` 해시로 순환 선택된다. **파일이 하나도 없으면 기동에 실패한다**(조용한 폴백 금지).
