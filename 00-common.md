# MCM Sync-Space — 공통 문서 (v2)

Dev A / Dev B가 합의한 내용의 **단일 소스(Single Source of Truth)**.
이 문서, 특히 §5 인터페이스 계약을 바꿀 때는 반드시 두 사람이 합의하고, 코드 변경과 같은 커밋에서 이 문서를 함께 갱신한다.

> **v2 개정 이력 (팀원 제공 제품 데이터 반영)**
> - 고객 태그: 2-STEP → **3-STEP** (도시 4종 · 목적/형태 4종 · **컬러 포인트 4종 신설**), 도시 무드 정의 전면 변경 — v1 태그 사전 폐기
> - §5 계약: `product.id`=SKU, `pattern` 삭제, `material`/`color_hardware`(원문 텍스트)·**`wear_position`** 추가, `style_hints.tpo`→`purpose`, 404·응답 4키 규칙 명문화
> - §8 태그 사전 전면 교체 (VLM 7축 도입), §9 페르소나 재정의
> - v1 매칭·생성 대상에서 **의류 제외** (DB 보관, matchable=false)

> **미결 사항 (문서 갱신 대기)**
> 1. 3-STEP 채택에 따른 피칭 문구 수정("2번의 감성 태그" → "태그 3번") — 발표자·기획 합의 필요
> 2. ~~스카프(neck) 생성 포함 여부~~ → **해소 (R2 · 2026-08-16): 포함.** 승자 모델(gpt-image-2)
>    출력이 c1·c3·c4·c5 동시 pass. §8 wear_position의 `neck` 확정, B의 `matchable` 플래그에 반영
> 3. 도쿄·파리·뮌헨 에디션 데이터 — 팀원에게 수집 템플릿 전달됨, 도착 시 §9 페르소나 SKU 확정

---

## 1. 목표

- 산출물: **무대 발표용 라이브 데모** — 모바일 웹에서 사진 1장 업로드 + 태그 3회 선택 → AI 시착 화보 생성 → 원클릭 매장 예약 → 매니저 태블릿에 예약 정보 표시.
- 품질 우선순위: **① 생성 화보의 제품 정합성 → ② 화보 감성/톤 → ③ UI 완성도 → ④ 속도**.
  속도는 엔진이 아니라 로딩 연출로 커버한다(Dev B 담당).

---

## 2. 확정 스코프

### 포함 (P0)

| 항목 | 담당 |
|---|---|
| 모바일 웹 전체 플로우 (업로드 → 태그 3회 → 로딩 → 화보 → 예약) | B |
| 상품 DB (에디션별 12 SKU × 4도시 목표, 팀원 공급 데이터 기반) | B |
| VLM 고객 사진 분석(7축) + 매칭 엔진 | B |
| 생성 엔진 (사진+상품 → 화보) | A |
| 매니저 뷰 (태블릿, 폴링) | B |
| 데모 모드 (§9) | A(자산) + B(구현) |

### 제외 / 축소

- **의류(스커트·팬츠) 매칭·생성** → v1 제외. DB에는 보관(matchable=false), 스트레치(P2)
- **Pinecone 등 벡터 DB** → 태그 필터 + 오버랩 스코어링 + LLM 사유 생성으로 대체
- **스마트 미러 / 매장 크로스셀링** → 발표 슬라이드 목업
- **실시간 푸시** → 폴링 / **회원·로그인** → localStorage 세션 ID
- **"3초 생성"** → 목표 60초 내, 체감은 로딩 연출로 커버
- **배경을 도시 풍경으로 바꾸는 연출** → 스트레치(P2). **v1은 원본 사진 배경·인물 유지, 상품 합성 + 톤 보정까지만.**

---

## 3. 역할 경계

| | Dev A | Dev B |
|---|---|---|
| 소유 | 생성 엔진 그 자체: **(사람 사진 + 확정된 상품 이미지 + 착용 위치) → 화보** 변환 | 그 외 전부: 웹, 상품 DB, VLM 분석, 매칭, 오케스트레이션, 예약/매니저 뷰, 데모 모드 |

- 호출 방향은 **B → A 단방향**. A는 순수 변환 서비스처럼 동작한다.
- **VLM 분석 결과는 B 내부 전용**이며 계약에 포함하지 않는다. A에게는 `style_hints`(city/purpose/lighting)와 `product.wear_position`만 흘러간다.
- 생성이 막혀도 B를 끌어들이지 않는다. 스코프를 줄이는 방향(사전 생성 피벗)으로만 움직인다.

---

## 4. 아키텍처 & 스택

```
[팀원(비개발)] ──수집 템플릿(xlsx)──> [Dev B 시드 스크립트] ──> Supabase
[모바일 웹  Next.js/Vercel — Dev B]
   │ (전부 server-side API Routes)
   ├──> Supabase (Postgres + Storage)
   ├──> VLM API (고객 사진 분석 — B 내부 전용)
   └──> [Gen Service  FastAPI/Railway — Dev A] ──> 외부 이미지 생성 API
[매니저 태블릿] ── 폴링 ──> 웹의 /manager
```

| 구성 | 선택 | 이유 |
|---|---|---|
| 웹 | Next.js(App Router) + TypeScript + Tailwind + shadcn/ui, Vercel | AI 코딩 도구 최적, 배포 마찰 최소 |
| 데이터 | Supabase (Postgres + Storage) | DB·스토리지·대시보드 일체형 |
| 생성 서비스 | Python 3.11 + FastAPI, Railway | 이미지 API 생태계, 장시간 작업 타임아웃 없음 |
| VLM/LLM | Gemini Flash 계열 또는 GPT-4o 계열 (structured output) | 분석·매칭용 |
| 이미지 생성 | **A의 베이크오프로 결정** (Dev A 문서 §2) | 셀프호스팅(SD+ControlNet) 금지 |

---

## 5. 인터페이스 계약 — Generation API (v2)

- Base URL: 환경변수 `GEN_BASE_URL` / 인증: 모든 요청 헤더 `X-API-Key: {GEN_API_KEY}` (`/health` 제외)
- **브라우저 직접 호출 금지** — B의 API 라우트가 서버사이드 프록시 (키 보호, CORS 불필요)
- **이미지는 항상 URL로 전달, base64 금지** (공개 읽기 가능한 Supabase Storage URL)

### POST /generate

```jsonc
// Request
{
  "user_photo_url": "https://.../assets/uploads/{session}/{ts}.jpg",   // 필수
  "product": {
    "id": "MMRGATA04CO001",              // 필수. MCM 스타일 넘버(SKU)를 그대로 사용
    "name": "나파 가죽 트림 비세토스 모노그램 캔버스 Aren 미디엄 크로스바디 백",
    "category": "crossbody",             // §8 카테고리 slug
    "material": "비세토스 모노그램 캔버스, 나파 가죽 트림, 코튼 트윌 안감",   // DB '소재 구성' 원문
    "color_hardware": "꼬냑, 24K 골드 도금 브라스 플레이트",                // DB '컬러&하드웨어' 원문
    "wear_position": "cross",            // 필수. §8 참조: hand|shoulder|cross|back|neck
    "ref_image_urls": ["https://.../products/MMRGATA04CO001/1.jpg"]    // 필수, §7.1 스펙 준수
  },
  "style_hints": {                       // 선택. A는 품질에 도움될 때만 사용, 무시 가능
    "city": "milano", "purpose": "daily", "lighting": "natural"        // 또는 null
  }
}

// 202: { "job_id": "j_a1b2c3d4e5f6" }
// 400: { "error_code": "BAD_INPUT", "message": "..." }
```

`pattern` 필드는 v2에서 삭제 — 시그니처 패턴 정보는 `name`/`material` 원문에 포함되어 A의 프롬프트에 그대로 쓰인다.

### GET /generate/{job_id}

```jsonc
// 200 — 아래 4개 키를 항상 포함 (해당 없으면 null, 키 생략 금지)
{
  "status": "queued" | "processing" | "done" | "failed",
  "image_url": null,                     // done일 때만 URL
  "error_code": null,                    // failed일 때만 BAD_INPUT|UPSTREAM_ERROR|TIMEOUT|UNKNOWN
  "elapsed_ms": 18234
}

// 존재하지 않는 job_id → 404: { "error_code": "UNKNOWN", "message": "job not found" }
```

### GET /health → `200 "ok"`

### 폴링 / 타임아웃 규약

- B: 2초 간격 폴링, 최대 60초 대기. 초과 또는 `failed` → 신규 POST 1회 재시도 → 실패 시 데모 모드 폴백(§9).
- A: upstream 자체 타임아웃 관리, **60초 내 반드시 done/failed 종결**. 영원한 processing 금지.

### Mock 규약

- A는 실엔진과 무관하게 **가장 먼저** 이 계약대로 동작하는 mock을 배포하고, B는 mock 기준으로 전체 플로우를 연결한다. 이후 A는 내부 구현만 교체.
- Mock 단계 전용 `X-Mock-Scenario` 헤더(실패·지연 시뮬레이션)는 `03-mock-service-spec.md` §5 참조. 실엔진 교체 후 무시된다.

---

## 6. 스토리지 규약

Supabase Storage, 버킷 `assets` (public read).

| 경로 | 용도 | 쓰기 |
|---|---|---|
| `/uploads/{session_id}/{ts}.jpg` | 사용자 업로드 원본 | B |
| `/products/{sku}/{n}.jpg` | 상품 레퍼런스 컷 (§7.1 스펙) | B |
| `/generated/{job_id}.jpg` | 생성 결과 | A |

데모 자산은 스토리지가 아닌 **레포 정적 파일로 커밋** (§9).

---

## 7. 입력 스펙 (A 확정 → B 반영, 순서 의존)

정의 권한은 A. **확정 전까지 B는 대량 이미지 수집과 업로드 검증 구현을 시작하지 않는다.**
베이크오프 샘플 3종은 예외적으로 즉시 확보: **No.08 (MMPGADC01BK001, 카프스킨 무지 가죽) / No.09 (MMRGATA04CO001, 비세토스 패턴) / No.10 (MMKGATA03MT001, 나일론)**.

### 7.1 상품 레퍼런스 이미지 스펙 — **확정 (R2 · 2026-08-16)**

| 항목 | 값 |
|---|---|
| 배경 | 화이트/뉴트럴 팩샷 |
| 필요 각도 / 장수 | **정면 1장 필수** + **보조컷 1장 권장** (각도는 지정하지 않는다 — 사선 3/4·탑다운·후면 모두 유효), 상한 3장 |
| 최소 해상도 | 정면컷 **긴 변 1024px 이상**. 보조컷은 하한 미달(578×842)에서도 회귀 미관측 |
| 기타 금기 | 모델 착용컷 / 워터마크 / 로고 가림 / 배경 소품 |

> **수집 우선순위 (B용)**: 전 SKU 정면 1장을 먼저 확보하고, 보조컷은 있는 것부터 붙인다.
> 보조컷 없이도 R2에서 9/9 전항목 pass였다 — **보조컷은 합격선 조건이 아니라 제품 동일성 개선분**이다.
> 근거: R2c(gpt, 3 SKU × 1장 vs 2장 × N2). 2장 쪽이 제품 동일성이 눈에 띄게 개선.
> 지연 페널티 없음(p95 43.1s → 42.8s), 비용 +$0.01/장 수준.
> **각도를 지정하지 않는 이유**: 세 종류(사선 3/4·탑다운·후면)를 모두 투입했으나 특정 각도의
> 우위가 관측되지 않았다. 각도를 지정하면 근거 없이 수집 난이도만 올린다.

### 7.2 사용자 사진 스펙 — **확정 (R2 · 2026-08-16)**

| 항목 | 값 |
|---|---|
| 프레이밍 | **무릎 위 이상** (전신 권장) |
| 인원 | 1명 |
| 최소 해상도 | **긴 변 1024px 이상** |
| 조명 권고 / 금기 | 균일광 권장 / 강한 역광·극단 각도·얼굴 가림 금기 |

> **이 표는 안전마진이다 — R2에서 하한을 때린 사례가 하나도 없다.** 실제로 통과시킨 조건:
> 짧은 변 686px / 허벅지 위 크롭(무릎 위보다 타이트, cross·shoulder 전항목 pass) /
> 팔짱 + 몸통 일부 가림 / 얼굴 3/4 측면 + 머리 상단 잘림 / 야외 자연광·스튜디오 인공광 모두.
> **금기 항목(역광·극단 각도·얼굴 가림)은 R2에서 검증되지 않았다** — 반증도 확보하지 못했으므로
> 예방적으로 유지한다. R4의 분산·엣지 실험에서 재검토한다.

---

## 8. 공용 태그 사전 (v2 — v1 전면 폐기)

**slug는 프론트, DB, API, 프롬프트에서 동일하게 사용하며 불변.** ⚠️ 도시 slug는 v1과 같지만 **무드 정의가 완전히 다르다** — 실제 MCM 에디션 축 기준.

### STEP 1 — 도시/무드 (에디션 축, 1개 선택)

| slug | 표시명 | 무드 | 매칭: 해당 에디션 SKU로 하드 필터 |
|---|---|---|---|
| milano | #밀라노 | 스트리트 / 트렌디 | 과감한 믹스매치 |
| tokyo | #도쿄 | 비즈니스 / 미니멀 | 정갈한 오피스룩 |
| paris | #파리 | 이브닝 / 시크 | 드레스업 파티룩 |
| munich | #뮌헨 | 헤리티지 / 트래블 | 클래식 럭셔리, 위켄드룩 |

### STEP 2 — 목적 & 형태 (1개 선택)

| slug | 표시명 | 형태 의도 |
|---|---|---|
| commute | #출근·미팅 | 각 잡힌 형태 (토트·브리프케이스·쇼퍼) |
| daily | #데일리·산책 | 드레이프 쉐입 (숄더·호보) |
| active | #활동성·이동 | 양손 자유 (백팩·메신저) |
| outing | #외출·파티 | 콤팩트 포인트 (크로스바디·미니) |

### STEP 3 — 컬러 포인트 (1개 선택, v2 신설)

| slug | 표시명 | 범위 |
|---|---|---|
| mono | #모던_무채색 | 블랙·그레이·실버 |
| cognac | #아이코닉_꼬냑 | 헤리티지 브라운·골드 |
| neutral | #소프트_뉴트럴 | 베이지·크림·샌드 |
| vivid | #비비드_컬러팝 | 원색·모노그램 패턴 |

### VLM 분석 7축 (B 내부 전용 — 제품의 '적합 착장 태그'와 오버랩 매칭)

| 축 | slug 값 |
|---|---|
| formality (①포멀도) | casual / smart_daily / business_semi / full_formal / evening |
| silhouette (②실루엣·핏) | oversized_long / slim_tailored / crop_short / loose_wide |
| outfit_material (③소재감) | wool_cashmere / leather / cotton_denim / silk_satin / tech_nylon |
| color_harmony (④컬러 조화) | mono_tone / earthy_neutral / contrast / vivid_color |
| pattern (⑤패턴) | solid / pattern_point |
| metal_tone (⑥메탈 톤) | gold / silver |
| gender *(시트에 없으나 유지)* | female / male / unisex |
| lighting *(시트에 없으나 유지, A로 전달되는 유일한 분석값)* | natural / warm / night |

### wear_position (⑦합성 위치 — 제품 속성이자 계약 필드)

`hand`(손 파지) / `shoulder`(어깨) / `cross`(크로스) / `back`(등) / `neck`(스카프, **R2 검증 완료 — 포함 확정**) / `garment`(의류 — **v1 생성·매칭 제외**)

### category slug

backpack / shoulder / shopper / tote / crossbody / vanity / drawstring / scarf / apparel(의류 총칭, matchable=false)

---

## 9. 데모 모드 (정식 P0 기능)

목적: 발표장 네트워크·외부 API 장애·생성 뽑기 실패 전부 무력화 + 일반 모드 실패 폴백.

- 진입: `?demo=1` → 랜딩에 페르소나 선택 UI (발표자용, 숨김 스타일). 외부 API 0회 호출로 전체 플로우 재생, 로딩 연출은 12~15초 단축 재생.
- 자산: `apps/web/public/demo/{persona_id}/photo.jpg`, `result.jpg`, `meta.json`
- 분담: 자산 납품 = A / 토글·재생 구현 = B / 페르소나 사진 선정 = 둘이 함께.

### 페르소나 6종 (v2 — 새 태그 체계 기준)

| id | 태그 (도시+목적+컬러) | 인물/착장 | 매칭 SKU |
|---|---|---|---|
| p1 | milano + daily + cognac | 여성, 슬림 데일리, 얼씨 뉴트럴 착장 | **No.09** Aren 크로스바디 (확정) |
| p2 | milano + active + vivid | 남성, 오버핏 스트릿, 배색 착장 | **No.10** 노바 백팩 (확정) |
| p3 | milano + commute + mono | 셋업 포멀, 모노톤 | **No.08** 카프스킨 쇼퍼백 (확정) |
| p4 | tokyo + commute + mono | 남성, 비즈니스 미니멀 셋업 | 도쿄 에디션 도착 후 확정 |
| p5 | paris + outing + mono | 여성, 이브닝 드레스업 | 파리 에디션 도착 후 확정 |
| p6 | munich + daily + neutral | 여성, 클래식 위켄드룩 | 뮌헨 에디션 도착 후 확정 |

p1~p3은 즉시 자산 제작 가능. p4~p6은 데이터 도착 전까지 착수 보류.

---

## 10. 레포 & 환경변수

모노레포 1개. **main은 항상 데모 가능한 상태 유지.**

```
mcm-sync-space/
├── apps/web/            # Dev B — Next.js
│   └── public/demo/     # 데모 모드 정적 자산 (A 납품 → 커밋)
├── services/gen/        # Dev A — FastAPI
├── docs/                # 이 문서들
└── README.md
```

| 위치 | 변수 | 비고 |
|---|---|---|
| apps/web | `NEXT_PUBLIC_SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | |
| apps/web | `VLM_API_KEY` | Gemini 또는 OpenAI |
| apps/web | `GEN_BASE_URL`, `GEN_API_KEY` | A가 배포 후 전달 |
| services/gen | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | B가 세팅 후 전달 |
| services/gen | 이미지 생성 API 키 (베이크오프 결과) | |
| services/gen | `API_KEY` | 인바운드 검증, `GEN_API_KEY`와 동일 값 |

---

## 11. 완성 기준 (Demo-Ready)

- [ ] 골든 패스 라이브 1회 관통: QR 접속 → 사진 업로드 → 태그 3회 → 로딩 → 화보 → 예약 → 매니저 뷰 반영
- [ ] 동일 경로가 **데모 모드에서 외부 API 없이 100% 재현**
- [ ] 페르소나 6종 화보가 제품 정합성 체크리스트(Dev A 문서 §2) 전 항목 통과
