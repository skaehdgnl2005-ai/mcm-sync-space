# Mock Service — v1 → v2 마이그레이션 패치 노트

> **용도**: `03-mock-service-spec.md` v1 기준으로 **이미 완성된** mock 서비스를 계약 v2로 패치하기 위한 AI 코딩 세션용 작업 지시서. 이 문서만으로 작업 가능하도록 자기완결로 작성했다. 전체 v2 스펙은 `03-mock-service-spec.md`(v2), 계약 원본은 `00-common.md` §5(v2).

---

## 0. 작업 범위 — 먼저 읽을 것

**바뀌는 것**: `POST /generate`의 요청 스키마(product·style_hints)와 그 검증 로직, 그리고 README의 curl 예시. 그게 전부다.

**바뀌지 않는 것 (건드리지 말 것)**:
엔드포인트 경로 · HTTP 상태 코드 · `GET /generate/{job_id}` 응답 4키 · job 상태 전이(queued→processing→done/failed) · `X-Mock-Scenario` 시나리오 로직 · 인메모리 job store · 엔진 추상화 인터페이스(`engines/base.py` 시그니처) · 정적 샘플 서빙 · `X-API-Key` 인증 · 환경변수(신규 없음) · 배포 설정(`GEN_BASE_URL` 불변, 재배포만).

이번 패치를 명목으로 한 리팩토링, 구조 개선, 의존성 추가는 **전면 금지**한다.

---

## 1. 요청 스키마 변경 (`schemas.py`)

### 필드 diff

| 대상 | v1 | v2 |
|---|---|---|
| `product.id` | 슬러그 문자열 (예: `heritage-crossbody-cognac`) | **MCM SKU** (예: `MMRGATA04CO001`) — 형식 검증 없음, 의미만 변경 |
| `product.pattern` | 존재 (선택) | **삭제** |
| `product.material` | 슬러그성 문자열 | 유지하되 의미 변경 — DB '소재 구성' **원문 텍스트** |
| `product.color_hardware` | 없음 | **신설** (선택) — DB '컬러&하드웨어' 원문 텍스트 |
| `product.wear_position` | 없음 | **신설, 필수** — 예: `hand`·`shoulder`·`cross`·`back`·`neck` |
| `style_hints.tpo` | 존재 | **키 이름 변경** → `style_hints.purpose` |

### 패치 후 목표 모델 (참고용 — 기존 코드 스타일에 맞춰 적용)

```python
class Product(BaseModel):
    id: str                                # MCM SKU
    name: str | None = None
    category: str | None = None
    material: str | None = None            # 소재 구성 원문 텍스트
    color_hardware: str | None = None      # v2 신설
    wear_position: str                     # v2 신설, 필수
    ref_image_urls: list[str]

class StyleHints(BaseModel):
    city: str | None = None
    purpose: str | None = None             # v1의 tpo에서 이름 변경
    lighting: str | None = None
```

- 알 수 없는 추가 키는 **에러 없이 무시**(pydantic extra=ignore)한다. B가 과도기에 `tpo`나 `pattern`을 보내와도 400을 내지 말고 그냥 버린다 — 단, `wear_position` 부재는 아래 §2대로 400이다.

---

## 2. 검증 로직 변경

**추가**:
- `product.wear_position` 누락 또는 빈 문자열 → **400** `{"error_code":"BAD_INPUT","message":"<필드명과 사유>"}`.
- ⚠️ **함정 주의**: pydantic 필수 필드 위반의 FastAPI 기본 응답은 **422**다. 계약은 400 BAD_INPUT을 요구한다. v1에서 `user_photo_url`·`product.id`·`ref_image_urls` 누락을 400으로 변환하던 것과 **동일한 경로**(validation exception handler 또는 수동 검증)로 처리해야 한다. 이 항목이 이번 패치에서 유일하게 실수하기 쉬운 지점이다.

**유지 (회귀 금지)**:
- `user_photo_url` · `product.id` · `ref_image_urls`(1개 이상 http(s) URL) 필수 → 위반 시 400
- URL 값의 `data:` 스킴 → 400
- `wear_position`과 `style_hints`의 **값 자체는 enum 검증하지 않는다** — 미정의 slug도 통과 (유연성 규칙)

**삭제**: `pattern` 관련 참조가 코드 어디든 있으면 제거 (v1에서 검증 대상이 아니었으므로 대부분 스키마 필드 삭제로 끝난다).

---

## 3. README / curl 예시 갱신

README의 요청 바디 예시를 아래 v2 payload로 전면 교체:

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

---

## 4. 완료 기준 (패치 후 전부 curl로 재검증)

### 신규 (v2 델타)

- [ ] 위 v2 payload POST → **202**
- [ ] `wear_position` 누락 POST → **400 BAD_INPUT** (422가 아님을 반드시 확인)
- [ ] `wear_position: "elbow"` 같은 미정의 값 → **202** (enum 검증 없음)
- [ ] v1 형태 바디(`tpo` 포함, `wear_position` 없음) → **400** (tpo 때문이 아니라 wear_position 부재 때문 — 에러 메시지로 확인)
- [ ] `pattern` 키 포함 바디 → 에러 없이 무시되고 202

### 회귀 (v1 완료 기준 전항목 유지)

- [ ] `/health` 200 무인증 / 키 없이 POST → 401
- [ ] 정상 흐름: 202 → queued|processing → ~6초 후 done + image_url 브라우저 열림
- [ ] `user_photo_url` 누락 → 400 / `data:` URL → 400
- [ ] `X-Mock-Scenario` fail→UPSTREAM_ERROR / timeout→TIMEOUT / slow→60초 시점 processing
- [ ] 없는 job_id → 404 UNKNOWN / GET 응답 4키 항상 존재

---

## 5. 전환 절차 (B와 조율)

1. 패치 + 로컬 curl 전항목 통과
2. B에게 통보: "계약 v2로 전환, `03-mock-service-spec.md` v2 참조" — B가 아직 mock 연동 전이면 통보만으로 충분
3. Railway 재배포 (**`GEN_BASE_URL` 불변** — B의 env 변경 없음)
4. B의 스모크 테스트 1회

**호환 shim 금지**: `tpo` alias 지원, `pattern` 유지, v1/v2 버전 분기 같은 하위호환 코드를 만들지 않는다. 클라이언트는 B 하나뿐이고 B도 v2로 동시 전환한다. 과도기 안전장치는 §1의 "알 수 없는 키 무시"만으로 충분하다.
