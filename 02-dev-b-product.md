# Dev B — 프로덕트 전체 (Web / Data / Orchestration) (v2)

> **v2 개정 이력**: 상품 데이터 소스가 "B 수작업 큐레이션" → **"팀원 공급 파이프라인 + B 정규화·시드"**로 전환. 웹 플로우에 STEP3 컬러 화면 추가, VLM 스키마 7축+2 전면 교체, 매칭을 오버랩 스코어링으로 재설계, 커버리지 4×4, 의류 matchable=false, 페르소나 v2.

## 0. 미션

사용자가 보고 만지는 전부 + 데이터 + 흐름 제어. 두 관점 유지: ① **마법이 '보이게'** (로딩 연출, 리빌, 추천 이유) ② **발표가 '절대 안 죽게'** (데모 모드, 폴백, 스코프 컷). 생성 엔진 내부에는 관여하지 않는다.

---

## 1. [P0] 인프라 / 레포 셋업 (가장 먼저)

- 모노레포(공통 §10), Vercel 연결, Supabase 프로젝트 + `assets` 버킷(public read) → **서비스 키 A에게 즉시 공유**.
- Next.js App Router + TS + Tailwind + shadcn/ui, 모바일(375~430px) 우선.
- API 라우트 `maxDuration` 상향, 오케스트레이션 단계별 라우트 분리(§6).

---

## 2. [P0] 상품 데이터 — 팀원 공급 파이프라인 (v2 재편)

### 구조

1. **팀원(비개발)**: 에디션별 제품 메타데이터 수집. 밀라노 12 SKU 완료, 도쿄·파리·뮌헨은 **`MCM_제품데이터_정규화_및_수집템플릿.xlsx`의 템플릿 시트** 양식으로 수령.
2. **B**: 시드 스크립트 작성 — 정규화 시트(1행 1제품) → `products` 테이블 + Storage 적재. 태그 표기(#한글)를 공통 §8 slug로 변환하는 매핑 테이블 포함.
3. **갭 채우기**: 현재 데이터에 **이미지·가격·성별 없음**. 성별·가격은 공식몰 상품 페이지에서 팀원과 분담, **이미지 대량 수집은 A의 상품컷 스펙(공통 §7.1) 확정 후** — 순서 의존 유효.

### 즉시 처리 (스펙 확정 예외)

베이크오프 샘플 3종(No.08/09/10, SKU는 공통 §7)의 공식몰 이미지를 **바로 확보해 A에게 전달**.

### 규칙

- 의류(apparel) 카테고리: DB 적재하되 `matchable=false` — 매칭·생성에서 제외 (공통 §2).
- 스카프: A의 베이크오프 결정 전까지 `matchable=false`, 확정 시 전환.
- 커버리지: **도시(4) × 목적(4) = 16조합** 각각 matchable 상품 ≥1 검증 (밀라노는 4개 목적 전부 커버 확인됨). 컬러(step3)까지 비면 매칭 폴백(§5)이 흡수하는지 확인.

### 스키마 (v2)

```sql
products(
  sku text pk,                 -- MCM 스타일 넘버, product.id로 그대로 사용
  name, category, gender, price int,
  size_spec, materials, color_hardware,      -- 원문 텍스트 (계약·프롬프트에 그대로 전달)
  wear_positions text[],                     -- hand|shoulder|cross|back|neck|garment
  step1_city, step2_purpose text[], step3_color text[],
  fit_tags jsonb,   -- {formality:[], silhouette:[], outfit_material:[], color_harmony:[], pattern:[], metal_tone:[]}
  image_urls text[], matchable bool default true
)
reservations( id uuid pk, session_id, sku, generated_image_url,
  persona_summary jsonb, store, slot, created_at )
```

---

## 3. [P0] 모바일 웹 플로우 (v2 — 태그 3화면)

럭셔리 톤 — 블랙/코냑, 여백, 세리프 헤드라인.

| # | 화면 | 핵심 요구사항 |
|---|---|---|
| 1 | 랜딩 | 브랜드 톤 인트로 + 시작 CTA |
| 2 | 사진 업로드 | A의 사용자 사진 스펙(공통 §7.2) 반영 — 실루엣 가이드, 클라이언트 검증, 촬영/앨범, 즉시 Storage 저장 |
| 3 | STEP1 도시 | 4종 카드 — "당신의 착장, 어느 도시를 닮았나요?" |
| 4 | STEP2 목적·형태 | 4종 — "그곳에서 당신의 발걸음과 가방은?" |
| 5 | STEP3 컬러 포인트 | 4종 — "당신을 완성할 결정적 포인트는?" |
| 6 | 로딩 시어터 | "스타일 분석 중 → 컬렉션 매칭 중 → 화보 렌더링 중", 15~60초 체감 커버 |
| 7 | 결과 리빌 | 화보 풀스크린 + 상품 카드(이름/소재/컬러&하드웨어/가격) + **매칭 이유 한 문장** + 예약 CTA |
| 8 | 예약 | 매장 + 시간 슬롯 → 원클릭 확정 |
| 9 | 매니저 뷰 `/manager` | 태블릿 가로, 예약 리스트(시간/페르소나 요약/SKU/화보 썸네일), 5초 폴링, 비공개 URL |

태그 3화면은 선택 즉시 자동 진행(버튼 탭 1회 = 화면 전환)으로 마찰 최소화 — "터치 3번" 피칭과 일치.

---

## 4. [P0] VLM 고객 분석 (v2 스키마 — 내부 전용)

- 모델: Gemini Flash 또는 GPT-4o 계열, structured output 필수. enum 값은 공통 §8 slug와 동일.

```jsonc
{
  "formality":       ["smart_daily"],          // 1~2개
  "silhouette":      ["slim_tailored"],
  "outfit_material": ["cotton_denim"],
  "color_harmony":   ["earthy_neutral"],
  "pattern":         ["solid"],
  "metal_tone":      ["gold"],                 // 착용 주얼리·시계·버클 기준
  "gender": "female",                          // 시트에 없으나 유지 (필터용)
  "lighting": "natural"                        // 시트에 없으나 유지 — A에게 가는 유일한 분석값
}
```

- `lighting`만 `style_hints`로 전달, 나머지는 매칭 전용. 스키마를 계약에 노출 금지.

---

## 5. [P0] 매칭 엔진 (v2 — 오버랩 스코어링)

1. **하드 필터**: `step1_city` = 선택 도시, 선택 목적 ∈ `step2_purpose`, `matchable=true`, gender 호환(unisex 포함).
2. **컬러 필터**: 선택 컬러 ∈ `step3_color`. 결과 0개면 이 필터부터 해제(폴백 1단계).
3. **오버랩 스코어**: 고객 VLM 태그(6축) ∩ 제품 `fit_tags` 교집합 개수 합산. **metal_tone 일치 시 가중 부스트** (골드 주얼리 → 골드 하드웨어 — 결과 화면 이유 문장의 킬러 소재).
4. **LLM 마무리**: 상위 2~3개 후보 + 고객 분석 JSON → 최종 1개 선택 + **추천 이유 한 문장** 생성.
- 폴백 순서: step3 해제 → 스코어 상위 무조건 선택. 절대 빈 결과 금지. 매칭 로그 저장.

---

## 6. [P0] 오케스트레이션

| 라우트 | 역할 |
|---|---|
| `POST /api/session/upload` | Storage 저장 → `photo_url` |
| `POST /api/analyze` | VLM + 매칭 → `{ product(wear_position 포함), style_hints, rationale }` |
| `POST /api/generate/start` | A의 `POST /generate` 프록시 (계약 v2 payload) → `job_id` |
| `GET /api/generate/status` | A의 GET 프록시 (클라이언트 2초 폴링) |

실패: 60초 초과/`failed` → 재시도 1회 → 데모 자산 폴백 + 자연스러운 카피. 에러 화면을 심사위원에게 노출하지 않는다.

---

## 7. [P0] 데모 모드

- `?demo=1` → 페르소나 6종(공통 §9 v2) 선택 UI. `public/demo/*` 정적 자산만으로 전체 플로우 재생(외부 API 0회), 로딩 12~15초.
- p1~p3은 A 자산 도착 즉시 연결, p4~p6은 도시 데이터 대기. 발표용 고정 URL + QR 준비.

## 8. [P0] 예약 + 매니저 뷰

`persona_summary`(태그 3종 + VLM 요약 + 매칭 이유) 저장 → 매니저 뷰 노출. "매장이 고객을 미리 안다"의 증명 장치.

## 9. [P1] 폴리싱

화보 저장/공유(OG 메타태그), "다른 스타일 보기"(차순위 SKU 재생성), 리빌 애니메이션, 매니저 상세 모달.

## Non-goals

생성 엔진 내부 — A 소유. B의 대응은 폴백과 스코프 컷뿐.
