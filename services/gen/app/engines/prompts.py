"""프롬프트 뱅크 — 06-gen-research-tools-spec.md §8 / 01-dev-a-generation.md §4.

★승격 완료 (R5) — `research/genlab/prompts.py`의 **복사본**이다. 원본과 프롬프트
문자열이 한 글자라도 달라지면 연구 결과가 실엔진을 설명하지 못하게 되므로, 이 파일은
`TEMPLATES`·`WEAR_POSITION_DIRECTIVES`·`DIRECTIVE_OVERRIDES`·조립 로직을 원본과
**동일하게** 유지한다 (이 모듈 docstring만 다르다). import는 stdlib만 (06 §1 N5) —
research/를 import하면 Railway 빌드 컨텍스트 밖 참조로 배포가 깨진다.

pv2 = pv1 동결(05 부록 A-13 ⑦)이므로 `"pv2"`라는 키는 **존재하지 않는다.** 실엔진
어댑터는 `"pv1"`을 넘긴다 (engines/live.py). 별칭 키를 만들지 마라 — 별칭은 "pv2가
따로 있다"는 착각을 만들고, 다음 사람이 pv2만 고쳐 동결을 조용히 깬다.

`product` 인자는 **계약 필드명 dict**다 (00-common §5 / schemas.py의 Product).
- 연구:   products.yaml 항목
- 실엔진: payload.product.model_dump()
둘이 같은 형태이므로 승격 시 조립기는 무수정으로 돈다. 이것이 이 파일의 존재 이유다.

버전 규율: TEMPLATES는 **추가만, 수정 금지**. manifest에 남은 prompt_text가
"pv1이 무엇이었는가"의 유일한 기록이므로, pv1을 고치는 순간 과거 실행이 재현
불가능해진다. 개선은 반드시 새 키(pv2…)로.
"""

from __future__ import annotations

__all__ = [
    "WEAR_POSITION_DIRECTIVES",
    "DIRECTIVE_OVERRIDES",
    "TEMPLATES",
    "assemble",
    "known_versions",
]

# 01 §4 "wear_position → 합성 지시 매핑" 표 5종.
WEAR_POSITION_DIRECTIVES: dict[str, str] = {
    "hand": (
        "Carried in one hand by the top handle, or hooked over the forearm. "
        "The grip must look natural: fingers wrap the handle, the bag hangs "
        "under its own weight."
    ),
    "shoulder": (
        "Worn on ONE shoulder with the strap over the shoulder, the bag resting "
        "against the side of the torso at hip-to-waist height. The strap must "
        "sit on top of the clothing and compress it slightly."
    ),
    "cross": (
        "Worn crossbody: the strap runs diagonally across the torso from one "
        "shoulder to the opposite hip, and the bag rests at the hip. The strap "
        "must be continuous and pass behind the arm, not floating over it."
    ),
    "back": (
        "Worn on the back, both straps over the shoulders. If the person faces "
        "the camera, show ONLY the shoulder straps and the small part of the bag "
        "visible past the torso silhouette — keep the body of the bag hidden "
        "behind the person. Do NOT rotate, mirror, or re-pose the person to show "
        "their back."
    ),
    "neck": (
        "Draped around the neck as a scarf: looped once with the two ends falling "
        "over the chest, fabric folds and drape following gravity. The weave and "
        "print scale must stay true to the reference."
    ),
}

_FALLBACK_DIRECTIVE = (
    "Worn at the body position '{slug}', in the most natural way for this product type."
)
"""wear_position은 계약상 enum 검증을 하지 않는다 (schemas.py §Product).
모르는 slug가 들어와도 조립이 죽으면 안 된다 — 실엔진에서는 곧 500이다."""

_PV1 = """\
Edit the provided photograph of a person so that they are wearing or carrying the \
exact product shown in the reference image(s).

This is a PHOTO EDIT, not a new photo. The person, their clothing and the original \
background all stay as they are; the only addition is the product.

PRODUCT — the reference image(s) are authoritative. Reproduce that exact item. The \
Korean text below is the official product record for the same item; use it to resolve \
material, colour and hardware details:
{product_facts}

PLACEMENT — {wear_directive}

PRESERVE
- The person's face, identity, skin tone, body proportions, hands and hair: unchanged.
- The outfit already in the photo: unchanged in cut, colour and texture.
- The original background, camera angle and framing: unchanged.
- Lighting integration: match the existing light direction, colour temperature and \
contrast, and add a correct contact shadow where the product touches the body or \
ground, so the product belongs to the scene instead of looking pasted on.
- Scale: the product must read at its real-world size relative to the person.{style_directive}

DO NOT
- Do not redesign, restyle or "improve" the product. The monogram/pattern, its repeat \
scale, the logo plate lettering, the stitching and the hardware shape must match the \
reference exactly.
- Do not change the hardware tone: gold stays gold, silver stays silver.
- Do not change the product colour.
- Do not distort the face, fingers or body; do not slim, reshape or beautify the person.
- Do not replace, blur or relight the background beyond the tonal match described above.
- Do not add any text, watermark, logo or extra accessory that is not in the reference.

OUTPUT — one photorealistic image, vertical 3:4, editorial fashion-campaign quality.\
"""

_CROSS_PV1C = (
    "Worn crossbody. The strap is ONE continuous band running from the near "
    "shoulder, diagonally across the chest, down to the opposite hip, where the "
    "body of the bag rests against the hip — tucked beside or behind the forearm, "
    "never in front of it.\n"
    "Decide the strap's visibility segment by segment, from what actually overlaps "
    "in THIS photograph:\n"
    "- Where the arm lies in front of the torso, the arm covers the strap. Omit it there.\n"
    "- Wherever there is a gap between the inner edge of the arm and the side of the "
    "torso, the strap IS VISIBLE through that gap and must be drawn inside it.\n"
    "The strap must not stop, fade or end at the edge of the arm, and must never lie "
    "on top of the front of the arm. A strap that disappears where the arm meets the "
    "body makes the bag read as broken and pasted on."
)
"""R3 축⑥ (pv1c) — cross 지시만 교체. 05 부록 A-9 ①이 특정한 결함을 겨눈다.

관측된 실패: 정면·직립·팔 내림 자세에서 **팔과 몸통 사이 틈으로 보여야 할 스트랩 구간을
그리지 못한다** (gpt "팔과 몸통 사이에서 끝나야", gemini "팔과 몸 사이에 공간이 있으면 뒤에
스트랩이 살짝 보여야 하는데 이 표현이 없음"). 두 벤더가 같은 방식으로 실패했다.

가설: pv1의 `pass behind the arm`이 **"팔 뒤에 가려진다"로 읽혀 해당 구간을 아예 생략**하게
만든다. 그래서 pv1c는 "뒤로 지나가라"는 경로 지시를 버리고 **가림(occlusion) 판정 규칙**으로
바꾼다 — 팔이 몸통을 덮는 곳에서만 가리고, 틈이 있으면 반드시 그린다.

pv1과의 차이는 **이 문자열 하나뿐**이다 (07 §4 "축별 독립 1:1"). 다른 wear_position 지시,
템플릿 본문, 부정 지시 블록은 전부 동일하다 — 그래야 차이의 원인이 이 문단으로 특정된다."""

_CROSS_GAP_RULE = (
    " Where the arm hangs away from the torso and a gap is visible between them, "
    "the strap must be drawn inside that gap — it must not stop or disappear at "
    "the edge of the arm."
)
"""pv1d가 pv1에 **덧붙이는 유일한 문장**. 축⑥(pv1c)의 가림 판정 규칙에서 좌우 문구를
건드리는 부분을 전부 걷어내고 남긴 알맹이다.

축⑥의 실패는 두 변경이 한 문단에 섞인 탓이었다 — (a) 가림 규칙 추가와 (b) 대각선 문구
약화. (b)가 좌우 결함을 새로 만들어 (a)까지 같이 죽었다(부록 A-10 ⑦).
pv1d는 (a)만 남긴다."""

DIRECTIVE_OVERRIDES: dict[str, dict[str, str]] = {
    "pv1c": {"cross": _CROSS_PV1C},
    # pv1d = pv1 원문 + 문장 1개. **문자열 연결로 만든다** — 원문을 손으로 옮겨 적으면
    # 그 과정에서 문구가 미끄러진다. 축⑥이 정확히 그렇게 실패했으므로 구조로 막는다.
    "pv1d": {"cross": WEAR_POSITION_DIRECTIVES["cross"] + _CROSS_GAP_RULE},
}
"""버전별 wear_position 지시 덮어쓰기. 비어 있으면 WEAR_POSITION_DIRECTIVES를 쓴다.

**왜 템플릿이 아니라 지시 사전을 갈랐나**: R3의 축들은 대부분 템플릿 본문을 건드리지만
축⑥은 wear_position 지시 한 줄만 바꾼다. 본문을 통째로 복사하면 두 버전의 diff에 잡음이
섞이고, 나중에 본문을 고칠 때 한쪽만 고치는 사고가 난다. 여기서는 **바뀌는 것만 적는다.**

manifest에는 조립된 `prompt_text` 전문이 남으므로 재현성은 그대로다."""

TEMPLATES: dict[str, str] = {
    "pv1": _PV1,
    # pv1c·pv1d는 본문이 pv1과 **동일**하다. 차이는 DIRECTIVE_OVERRIDES에만 있다.
    "pv1c": _PV1,
    "pv1d": _PV1,
}


def known_versions() -> tuple[str, ...]:
    return tuple(TEMPLATES)


def assemble(
    product: dict,
    style_hints: dict | None,
    version: str,
) -> str:
    """계약 필드명 dict + style_hints → 프롬프트 전문.

    슬롯: name / material / color_hardware (→ product_facts) + wear_position 지시
    + style_hints(lighting). 값이 없는 필드는 줄째로 빠진다 — 계약상 name·material·
    color_hardware는 Optional이고, "material: None"이 프롬프트에 새는 편이 더 나쁘다.
    """
    try:
        template = TEMPLATES[version]
    except KeyError:
        raise KeyError(
            f"unknown prompt version {version!r}; known: {', '.join(TEMPLATES)}"
        ) from None

    return template.format(
        product_facts=_product_facts(product),
        wear_directive=_wear_directive(product.get("wear_position"), version),
        style_directive=_style_directive(style_hints),
    )


def _product_facts(product: dict) -> str:
    # name → category → id 순 폴백. 계약상 name·category는 Optional이지만 id는 필수다.
    label = (
        _clean(product.get("name"))
        or _clean(product.get("category"))
        or _clean(product.get("id"))
        or "the item in the reference images"
    )
    lines = [f"- Product: {label}"]
    material = _clean(product.get("material"))
    if material:
        lines.append(f"- Material: {material}")
    color_hardware = _clean(product.get("color_hardware"))
    if color_hardware:
        lines.append(f"- Colour & hardware: {color_hardware}")
    return "\n".join(lines)


def _wear_directive(wear_position: object, version: str = "pv1") -> str:
    """버전 오버라이드 → 기본 사전 → 폴백 순. 오버라이드가 없는 버전은 pv1과 동일하게 돈다."""
    slug = _clean(wear_position).lower()
    directive = (DIRECTIVE_OVERRIDES.get(version) or {}).get(slug) or (
        WEAR_POSITION_DIRECTIVES.get(slug)
    )
    if directive:
        return directive
    return _FALLBACK_DIRECTIVE.format(slug=slug or "unspecified")


def _style_directive(style_hints: dict | None) -> str:
    """v1 정책: **lighting만** 활용한다.

    city 무드 언어는 00-common §8 v2에서 밀라노=스트리트·도쿄=미니멀로 v1과 뒤집혔고,
    v1 생성 정책은 '원본 배경 유지 + 톤 보정까지'라 배경을 바꾸는 무드 지시와 충돌한다.
    city/purpose 반영 여부는 R3 축④의 결론으로 pv2에서 정한다 (05 §3).
    """
    if not style_hints:
        return ""
    lighting = _clean(style_hints.get("lighting"))
    if not lighting:
        return ""
    return (
        "\n- Tone: grade the whole frame toward a "
        f"'{lighting}' lighting mood, without moving the light sources or changing "
        "the background contents."
    )


def _clean(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""
