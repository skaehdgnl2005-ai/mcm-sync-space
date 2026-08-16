"""자산 정규화 — 06-gen-research-tools-spec.md §9 (F6).

리사이즈(긴 변 1536, 비율 유지, **업스케일 없음**) + EXIF 제거 + 알파 평탄화 +
JPEG 변환 + 파일명 규약 + configs 레지스트리 대조 검증.

**독립 스크립트다** (06 §0) — genlab 내부 모듈을 import하지 않는다. 그래서
`python genlab/prep_assets.py`(06 §10)와 `python -m genlab.prep_assets` 양쪽으로
돌고, 러너가 깨져 있어도 자산 준비는 가능하다. 승격 대상은 아니므로 yaml·PIL은 쓴다.

왜 각 단계가 필요한가:
- **EXIF 제거**: GPS 등 개인정보가 외부 API로 전송되는 것을 막는다. 회전 태그는
  버리기 **전에** 실제 픽셀에 적용한다(exif_transpose) — 안 그러면 메타데이터를
  지우는 순간 사진이 눕는다.
- **알파 평탄화(→ 흰색)**: 공식몰 팩샷은 투명 배경 PNG다. RGB로 그냥 변환하면
  투명부가 **검정**이 되어 팩샷이 통째로 망가진다. 05 부록 B §7.1의 "화이트/뉴트럴
  팩샷" 규격에 맞춰 흰색으로 합성한다.
- **JPEG 변환**: 레지스트리가 .jpg를 가리킨다. 4:4:4 + q95로 저장하는 이유는
  비세토스 모노그램·로고 플레이트 문자가 c1 판정 대상이라 크로마 서브샘플링이
  그대로 채점 오염이 되기 때문이다.
- **긴 변 1536**: 전송량·비용 절감(05 §7 "전송 전 리사이즈"). 업스케일은 하지
  않는다 — 없는 디테일을 만들어 내면 c1 판정이 입력 해상도 착시로 오염된다.

사용법 (PowerShell):
    python genlab/prep_assets.py --all                     # assets/ 전체 제자리 정규화
    python genlab/prep_assets.py --all --dry-run           # 계획만
    python genlab/prep_assets.py --verify                  # 레지스트리 대조만
    python genlab/prep_assets.py --kind selfie raw_in/ assets/selfies/
"""

from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml
from PIL import Image, ImageOps

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
CONFIGS_DIR = RESEARCH_ROOT / "configs"
ASSETS_DIR = RESEARCH_ROOT / "assets"
SELFIES_DIR = ASSETS_DIR / "selfies"
PRODUCTS_DIR = ASSETS_DIR / "products"

ORIGINALS_DIR = ASSETS_DIR / "_originals"
"""제자리 변환으로 대체된 원본이 옮겨 가는 곳. 삭제하지 않는 이유: assets/는 git
미추적이라 지우면 복구 수단이 없다. assets/ 하위이므로 함께 미추적이고, 레지스트리
대조에서는 제외된다."""

MAX_LONG_EDGE = 1536
"""06 §9. 이보다 작은 이미지는 그대로 둔다 (업스케일 금지)."""

MIN_LONG_EDGE_WARN = 1024
"""05 부록 B §7.1/§7.2의 '최소 해상도 긴 변 1024px 이상'. 미달은 경고만 한다 —
자산 교체는 사람의 판단이고, 스크립트가 실험을 막을 일은 아니다."""

MIN_SHORT_EDGE_WARN = 768
"""부록 B에는 없는 추가 가드. 3:4 출력의 짧은 변에 해당하는 값(1024 × 3/4)이다.

근거: 긴 변만 재는 규격은 380×1244 같은 세로 크롭을 통과시킨다 — 긴 변 1244px로
규격은 만족하지만 폭이 380px이면 얼굴이 ~45px라 c4(인물 보존)·c1(로고)이 입력
해상도 때문에 fail한다. 실측으로 드러난 구멍이라 막아 둔다."""

JPEG_QUALITY = 95
TARGET_EXT = ".jpg"
_SOURCE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
_FLATTEN_BG = (255, 255, 255)


class PrepError(RuntimeError):
    """자산 준비를 계속할 수 없다."""


# --- 결과 수집 ---------------------------------------------------------------


@dataclass
class Report:
    converted: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def fail(self, message: str) -> None:
        self.errors.append(message)


def _rel(path: Path) -> str:
    try:
        return path.relative_to(RESEARCH_ROOT).as_posix()
    except ValueError:
        return str(path)


# --- 이미지 정규화 ------------------------------------------------------------


@dataclass(frozen=True)
class Normalized:
    width: int
    height: int
    resized: bool
    flattened: bool
    exif_dropped: int


def normalize_image(src: Path, dst: Path) -> Normalized:
    """1파일 정규화. dst는 항상 JPEG로 쓴다.

    원본을 **메모리로 먼저 읽는** 이유: 제자리 변환(src == dst)에서 읽기 핸들을
    연 채로 같은 경로에 쓰면 Windows에서 공유 위반이 날 수 있다.
    """
    raw = io.BytesIO(src.read_bytes())
    with Image.open(raw) as opened:
        exif_tags = len(opened.getexif() or {})
        # 회전 태그를 픽셀에 먼저 적용한다 — 그 뒤에야 메타데이터를 버릴 수 있다.
        img = ImageOps.exif_transpose(opened)
        img.load()

    flattened = "A" in img.mode or img.mode == "P"
    if flattened:
        # 팔레트 이미지도 투명도를 가질 수 있으므로 RGBA를 거쳐 합성한다.
        rgba = img.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, _FLATTEN_BG)
        canvas.paste(rgba, mask=rgba.split()[-1])
        img = canvas
    elif img.mode != "RGB":
        img = img.convert("RGB")

    long_edge = max(img.size)
    resized = long_edge > MAX_LONG_EDGE
    if resized:
        scale = MAX_LONG_EDGE / long_edge
        img = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.LANCZOS,
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    # exif·icc를 넘기지 않는 것이 곧 제거다. subsampling=0(4:4:4)은 모노그램
    # 반복 패턴이 채점 대상(c1)이라 크로마 손실을 감수할 수 없기 때문.
    img.save(dst, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)
    return Normalized(img.width, img.height, resized, flattened, exif_tags)


# --- 파일명 규약 --------------------------------------------------------------


def target_for(src: Path, kind: str, src_root: Path, dst_root: Path) -> Path:
    """파일명 규약 적용 — selfies: `{id}.jpg` / products: `{sku}/{n}.jpg`."""
    if kind == "selfie":
        return dst_root / (src.stem + TARGET_EXT)
    # product: SKU 디렉터리 구조를 보존한다.
    rel = src.relative_to(src_root)
    return dst_root / rel.parent / (src.stem + TARGET_EXT)


def check_naming(path: Path, kind: str, reg: "Registry", report: Report) -> None:
    if kind == "selfie":
        if path.stem not in reg.selfie_keys:
            report.warn(
                f"{_rel(path)}: 파일명 '{path.stem}'이 selfies.yaml의 키가 아니다 "
                f"(등록된 키: {', '.join(sorted(reg.selfie_keys)) or '없음'})"
            )
        return
    sku = path.parent.name
    if sku not in reg.product_skus:
        report.warn(
            f"{_rel(path)}: 상위 디렉터리 '{sku}'가 products.yaml의 SKU가 아니다"
        )
    if not path.stem.isdigit():
        report.warn(
            f"{_rel(path)}: 상품컷 파일명은 `{{n}}{TARGET_EXT}` 규약이다 "
            f"(숫자, 정면컷이 1) — 지금은 '{path.stem}'"
        )


# --- 레지스트리 --------------------------------------------------------------


@dataclass
class Registry:
    selfie_keys: set[str]
    product_skus: set[str]
    declared: dict[str, str]
    """레지스트리가 가리키는 상대경로 → 그 출처 설명."""


def load_registry() -> Registry:
    def _read(name: str) -> dict:
        path = CONFIGS_DIR / name
        if not path.exists():
            raise PrepError(f"missing registry: {_rel(path)}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise PrepError(f"{_rel(path)} must be a mapping at the top level")
        return data

    selfies = _read("selfies.yaml")
    products = _read("products.yaml")
    declared: dict[str, str] = {}

    for key, raw in selfies.items():
        file = str((raw or {}).get("file", "")).strip()
        if file:
            declared[file] = f"selfies.yaml[{key}].file"
    for sku, raw in products.items():
        for i, ref in enumerate(list((raw or {}).get("ref_images") or []), start=1):
            declared[str(ref).strip()] = f"products.yaml[{sku}].ref_images[{i}]"

    return Registry(set(selfies), set(products), declared)


def verify_registry(reg: Registry, report: Report) -> None:
    """양방향 대조 (06 §9): 레지스트리에 있는데 파일이 없다 = 오류(러너가 거부한다).
    파일은 있는데 레지스트리에 없다 = 경고(R3용 여분 컷일 수 있다)."""
    declared_paths: set[Path] = set()
    for rel_path, origin in sorted(reg.declared.items()):
        resolved = (RESEARCH_ROOT / rel_path).resolve()
        declared_paths.add(resolved)
        if not resolved.exists():
            report.fail(f"{origin} → {rel_path} 파일이 없다")
        elif resolved.suffix.lower() != TARGET_EXT:
            report.warn(f"{origin} → {rel_path} 확장자가 {TARGET_EXT}가 아니다")

    for root in (SELFIES_DIR, PRODUCTS_DIR):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SOURCE_EXTS:
                continue
            if ORIGINALS_DIR in path.parents:
                continue
            if path.suffix.lower() != TARGET_EXT:
                report.warn(
                    f"{_rel(path)}: 정규화되지 않은 원본이 남아 있다 "
                    "— prep_assets를 돌리거나 지워라"
                )
            elif path.resolve() not in declared_paths:
                report.warn(
                    f"{_rel(path)}: 레지스트리에 등록되지 않은 파일 "
                    "(R3 축① 여분 컷이면 정상)"
                )


# --- 오케스트레이션 -----------------------------------------------------------


def iter_sources(src_root: Path) -> Iterable[Path]:
    for path in sorted(src_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SOURCE_EXTS:
            continue
        if ORIGINALS_DIR in path.parents:
            continue
        yield path


def process(
    kind: str,
    src_root: Path,
    dst_root: Path,
    reg: Registry,
    report: Report,
    *,
    dry_run: bool,
    keep_originals: bool,
    log=print,
) -> None:
    if not src_root.exists():
        report.warn(f"{_rel(src_root)}: 디렉터리가 없다 — 건너뛴다")
        return

    for src in iter_sources(src_root):
        dst = target_for(src, kind, src_root, dst_root)
        check_naming(dst, kind, reg, report)

        # 이미 규약대로인 제자리 .jpg는 건드리지 않는다 — 매 실행마다 재인코딩하면
        # JPEG 세대 손실이 누적되어 c1 채점 대상이 조용히 열화된다.
        if src.resolve() == dst.resolve() and src.suffix.lower() == TARGET_EXT:
            with Image.open(src) as probe:
                size = probe.size
                needs_work = (
                    max(size) > MAX_LONG_EDGE
                    or bool(probe.getexif())
                    or probe.mode != "RGB"
                )
            if not needs_work:
                report.unchanged.append(_rel(src))
                _warn_if_small(dst, size, report)
                continue

        if dry_run:
            log(f"  would write {_rel(dst)}  <- {_rel(src)}")
            report.converted.append(_rel(dst))
            continue

        try:
            result = normalize_image(src, dst)
        except OSError as exc:
            report.fail(f"{_rel(src)}: 읽기/저장 실패 — {exc}")
            continue

        notes = []
        if result.resized:
            notes.append(f"resized→{result.width}x{result.height}")
        else:
            notes.append(f"{result.width}x{result.height} (업스케일 안 함)")
        if result.flattened:
            notes.append("alpha→white")
        if result.exif_dropped:
            notes.append(f"exif -{result.exif_dropped}")
        log(f"  {_rel(dst)}  [{', '.join(notes)}]")
        report.converted.append(_rel(dst))
        _warn_if_small(dst, (result.width, result.height), report)

        # 확장자만 바뀌어 제자리에서 대체된 원본만 보관 대상이다. 외부 raw 디렉터리
        # (src_root != dst_root)의 파일은 사용자 것이므로 손대지 않는다.
        superseded = src.resolve() != dst.resolve() and src.parent == dst.parent
        if superseded and not keep_originals:
            try:
                archived = ORIGINALS_DIR / src.relative_to(ASSETS_DIR)
            except ValueError:
                report.warn(f"{_rel(src)}: assets/ 밖이라 원본을 보관하지 않고 남겨 둔다")
                continue
            archived.parent.mkdir(parents=True, exist_ok=True)
            src.replace(archived)
            report.archived.append(_rel(archived))


def _warn_if_small(path: Path, size: tuple[int, int], report: Report) -> None:
    long_edge, short_edge = max(size), min(size)
    if long_edge < MIN_LONG_EDGE_WARN:
        report.warn(
            f"{_rel(path)}: 긴 변 {long_edge}px — 05 부록 B의 최소 {MIN_LONG_EDGE_WARN}px "
            "미달 (업스케일하지 않았다. 자산 교체를 검토하라)"
        )
    elif short_edge < MIN_SHORT_EDGE_WARN:
        # 긴 변만 보는 규격은 세로로 길쭉한 크롭을 통과시킨다. 3:4 출력에서 폭이
        # 곧 얼굴 픽셀 수이므로, 이쪽이 실제로 c1·c4를 결정한다.
        report.warn(
            f"{_rel(path)}: 짧은 변 {short_edge}px — 3:4 출력 기준 권장 "
            f"{MIN_SHORT_EDGE_WARN}px 미달 (긴 변 {long_edge}px는 규격을 통과하지만, "
            "폭이 얼굴·로고 디테일의 상한이다)"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python genlab/prep_assets.py",
        description="자산 정규화 + 레지스트리 대조 (06 §9)",
    )
    parser.add_argument(
        "--kind",
        choices=("selfie", "product"),
        help="처리할 자산 종류. --all과 함께 쓰지 않는다",
    )
    parser.add_argument("src", nargs="?", type=Path, help="원본 디렉터리 (생략 시 제자리)")
    parser.add_argument("dst", nargs="?", type=Path, help="출력 디렉터리 (생략 시 제자리)")
    parser.add_argument(
        "--all",
        action="store_true",
        help="assets/selfies·assets/products를 제자리 정규화",
    )
    parser.add_argument(
        "--verify", action="store_true", help="파일을 쓰지 않고 레지스트리 대조만"
    )
    parser.add_argument("--dry-run", action="store_true", help="계획만 출력")
    parser.add_argument(
        "--keep-originals",
        action="store_true",
        help=f"대체된 원본을 {_rel(ORIGINALS_DIR)}로 옮기지 않고 제자리에 둔다",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # Windows 콘솔 리다이렉트 시 cp949로 한국어가 깨진다 (06 §1 N1).
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    if not (args.all or args.verify or args.kind):
        build_parser().error("--all / --verify / --kind 중 하나는 필요하다")
    if args.all and args.kind:
        build_parser().error("--all과 --kind는 함께 쓰지 않는다")

    try:
        reg = load_registry()
    except PrepError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    report = Report()

    if not args.verify:
        jobs: list[tuple[str, Path, Path]] = []
        if args.all:
            jobs = [
                ("selfie", SELFIES_DIR, SELFIES_DIR),
                ("product", PRODUCTS_DIR, PRODUCTS_DIR),
            ]
        else:
            default_root = SELFIES_DIR if args.kind == "selfie" else PRODUCTS_DIR
            src = (args.src or default_root).resolve()
            dst = (args.dst or args.src or default_root).resolve()
            jobs = [(args.kind, src, dst)]

        for kind, src_root, dst_root in jobs:
            print(f"{kind}: {_rel(src_root)} -> {_rel(dst_root)}")
            process(
                kind,
                src_root,
                dst_root,
                reg,
                report,
                dry_run=args.dry_run,
                keep_originals=args.keep_originals,
            )

    verify_registry(reg, report)

    print(
        f"\nconverted={len(report.converted)} unchanged={len(report.unchanged)} "
        f"archived={len(report.archived)}"
    )
    for message in report.warnings:
        print(f"warn: {message}")
    for message in report.errors:
        print(f"ERROR: {message}", file=sys.stderr)
    if report.errors:
        print(
            f"\n{len(report.errors)}건의 오류 — 러너는 이 상태에서 실행을 거부한다.",
            file=sys.stderr,
        )
        return 1
    print("레지스트리 대조 통과 — 러너를 실행할 수 있다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
