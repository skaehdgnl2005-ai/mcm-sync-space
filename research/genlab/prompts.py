"""프롬프트 뱅크 — 06-gen-research-tools-spec.md §8 / 01-dev-a-generation.md §4.

★승격 대상: R5에서 이 파일이 `services/gen/app/engines/`로 **복사-이식**된다.
따라서 import는 stdlib만 (06 §1 N5). yaml·genlab 내부 모듈을 쓰면 안 된다.

`product` 인자는 **계약 필드명 dict**다 (00-common §5 / schemas.py의 Product).
- 연구:   products.yaml 항목
- 실엔진: payload.product.model_dump()
둘이 같은 형태이므로 승격 시 조립기는 무수정으로 돈다. 이것이 이 파일의 존재 이유다.

버전 규율: TEMPLATES는 **추가만, 수정 금지**. manifest에 남은 prompt_text가
"pv1이 무엇이었는가"의 유일한 기록이므로, pv1을 고치는 순간 과거 실행이 재현
불가능해진다. 개선은 반드시 새 키(pv2…)로.
"""

from __future__ import annotations

__all__ = ["WEAR_POSITION_DIRECTIVES", "TEMPLATES", "assemble", "known_versions"]

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

TEMPLATES: dict[str, str] = {"pv1": _PV1}


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
        wear_directive=_wear_directive(product.get("wear_position")),
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


def _wear_directive(wear_position: object) -> str:
    slug = _clean(wear_position).lower()
    directive = WEAR_POSITION_DIRECTIVES.get(slug)
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
