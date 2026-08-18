"""실험 러너 — 06-gen-research-tools-spec.md §5.

곱집합 전개 → dry-run 견적/예산 캡 → 동시성 실행 → 이미지 저장 + manifest append.
재개(F1)와 예산 거부(F7)가 이 파일의 두 핵심 안전장치다.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import yaml

from . import manifest, prompts, providers
from .providers.base import GenCall, GenOutcome, Stopwatch, sniff_image_ext

RESEARCH_ROOT = Path(__file__).resolve().parents[1]
"""configs/·assets/·experiments/의 기준점. cwd와 무관하게 동작시키기 위함이다."""

CONFIGS_DIR = RESEARCH_ROOT / "configs"
EXPERIMENTS_DIR = RESEARCH_ROOT / "experiments"

FAKE_SUFFIX = "_fake"
"""--fake 실행은 exp_id에 이 접미사를 붙여 별도 디렉터리로 격리한다.

이유: fake 산출물(=셀카 원본)이 진짜 실험 디렉터리에 섞이면, 나중 실전 실행의
재개 로직이 그것을 '완료된 셀'로 보고 스킵해버린다. $0 검증이 베이크오프를
조용히 오염시키는 사고를 구조적으로 막는다."""

_RETRYABLE = {"error"}
"""재시도는 네트워크·업스트림 오류 1회만 (06 §5-6).
- refused: 정책 거부는 재시도가 아니라 **데이터**다 (05 §7 리스크 1의 원천).
- timeout: 이미 타임아웃 예산을 다 쓴 시도다. 다시 걸면 벽시계만 2배가 된다
  (05 §7 "timeout 다발 → 기록 후 스킵")."""

_RETRY_BACKOFF_S = 2.0


class ConfigError(RuntimeError):
    """설정이 틀렸다 — 한 푼도 쓰기 전에 죽는다."""


class BudgetExceeded(RuntimeError):
    """견적이 budget_cap_usd를 넘었다 — 실행 거부 (F7)."""


# --- 레지스트리 --------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    key: str
    adapter: str
    model_id: str
    params: dict
    cost_per_image_usd: float
    enabled: bool


@dataclass(frozen=True)
class ProductSpec:
    sku: str
    name: Optional[str]
    category: Optional[str]
    material: Optional[str]
    color_hardware: Optional[str]
    wear_position: str
    ref_images: list[str]
    size: Optional[str] = None
    """DB '규격(사이즈)' 원문. **계약(schemas.py의 Product)에 없는 필드다.**

    c2("DB 사이즈 대비 명백한 과대·과소")를 사람이 판정할 때 쓰는 **채점 보조자료**이며,
    프롬프트에는 넣지 않는다. 넣으면 연구가 실엔진보다 유리한 조건으로 측정되어
    R2 판정이 낙관 편향된다 — 실엔진 payload에는 이 정보가 없기 때문이다.
    (넣을 가치가 있는지는 R3에서 1:1로 검증하고, 효과가 크면 계약 v3을 B와 협의한다.)
    """

    def contract_dict(self) -> dict:
        """schemas.py의 Product와 같은 형태 — prompts.assemble()이 실엔진에서 받는 것과
        동일한 dict를 연구에서도 넘긴다 (06 §8, 승격 무수정의 근거).

        키는 **명시 열거**다. products.yaml에 계약 밖 필드(size 등)가 늘어나도
        프롬프트로 새지 않는다 — 이 allowlist가 그 경계선이다.
        """
        return {
            "id": self.sku,
            "name": self.name,
            "category": self.category,
            "material": self.material,
            "color_hardware": self.color_hardware,
            "wear_position": self.wear_position,
        }


@dataclass(frozen=True)
class SelfieSpec:
    key: str
    file: str
    formality: Optional[str] = None
    note: Optional[str] = None


@dataclass(frozen=True)
class Registries:
    models: dict[str, ModelSpec]
    products: dict[str, ProductSpec]
    selfies: dict[str, SelfieSpec]


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping at the top level")
    return data


def load_registries(configs_dir: Path = CONFIGS_DIR) -> Registries:
    models = {}
    for key, raw in _load_yaml(configs_dir / "models.yaml").items():
        models[key] = ModelSpec(
            key=key,
            adapter=str(raw["adapter"]),
            model_id=str(raw.get("model_id", "")),
            params=dict(raw.get("params") or {}),
            cost_per_image_usd=float(raw.get("cost_per_image_usd", 0.0)),
            enabled=bool(raw.get("enabled", True)),
        )

    products = {}
    for sku, raw in _load_yaml(configs_dir / "products.yaml").items():
        refs = list(raw.get("ref_images") or [])
        if not refs:
            raise ConfigError(f"product {sku}: ref_images must list at least one file")
        products[sku] = ProductSpec(
            sku=sku,
            name=raw.get("name"),
            category=raw.get("category"),
            material=raw.get("material"),
            color_hardware=raw.get("color_hardware"),
            wear_position=str(raw["wear_position"]),
            ref_images=[str(item) for item in refs],
            size=raw.get("size"),
        )

    selfies = {}
    for key, raw in _load_yaml(configs_dir / "selfies.yaml").items():
        selfies[key] = SelfieSpec(
            key=key,
            file=str(raw["file"]),
            formality=raw.get("formality"),
            note=raw.get("note"),
        )

    return Registries(models=models, products=products, selfies=selfies)


# --- 실험 설정 ---------------------------------------------------------------

_CONFIG_KEYS = {
    "exp_id",
    "models",
    "selfies",
    "products",
    "prompt_versions",
    "reps",
    "concurrency",
    "budget_cap_usd",
    "style_hints",
    "timeout_s",
    "note",
}


@dataclass(frozen=True)
class ExperimentConfig:
    exp_id: str
    models: list[str]
    selfies: list[str]
    products: list[str]
    prompt_versions: list[str]
    reps: int
    concurrency_global: int
    concurrency_per_model: int
    budget_cap_usd: float
    style_hints: Optional[dict]
    timeout_s: float
    source: Path


def load_experiment(path: Path) -> ExperimentConfig:
    raw = _load_yaml(path)
    unknown = set(raw) - _CONFIG_KEYS
    if unknown:
        # override·중첩 문법이 없는 설계이므로(06 §3), 오타를 조용히 무시하면
        # 사용자는 "왜 안 먹지"를 돈 쓰며 디버깅하게 된다.
        raise ConfigError(
            f"{path}: unknown key(s) {sorted(unknown)}; allowed: {sorted(_CONFIG_KEYS)}"
        )

    def _str_list(key: str) -> list[str]:
        value = raw.get(key)
        if not isinstance(value, list) or not value:
            raise ConfigError(f"{path}: '{key}' must be a non-empty list")
        return [str(item) for item in value]

    concurrency = dict(raw.get("concurrency") or {})
    exp_id = str(raw.get("exp_id") or path.stem)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", exp_id):
        raise ConfigError(f"{path}: exp_id {exp_id!r} must be a safe directory name")

    reps = int(raw.get("reps", 1))
    if reps < 1:
        raise ConfigError(f"{path}: reps must be >= 1")

    return ExperimentConfig(
        exp_id=exp_id,
        models=_str_list("models"),
        selfies=_str_list("selfies"),
        products=_str_list("products"),
        prompt_versions=_str_list("prompt_versions"),
        reps=reps,
        concurrency_global=int(concurrency.get("global", 4)),
        concurrency_per_model=int(concurrency.get("per_model", 2)),
        budget_cap_usd=float(raw.get("budget_cap_usd", 0.0)),
        style_hints=dict(raw["style_hints"]) if raw.get("style_hints") else None,
        timeout_s=float(raw.get("timeout_s", 50.0)),
        source=path,
    )


# --- 전개 --------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    cell_id: str
    model: str
    selfie: str
    product: str
    prompt_version: str
    rep: int


def expand(config: ExperimentConfig) -> list[Cell]:
    """models × selfies × products × prompt_versions × reps — 곱집합 전개만."""
    cells: list[Cell] = []
    for model in config.models:
        for selfie in config.selfies:
            for product in config.products:
                for pv in config.prompt_versions:
                    for rep in range(1, config.reps + 1):
                        cells.append(
                            Cell(
                                cell_id=f"{model}__{selfie}__{product}__{pv}__r{rep}",
                                model=model,
                                selfie=selfie,
                                product=product,
                                prompt_version=pv,
                                rep=rep,
                            )
                        )
    return cells


def validate(config: ExperimentConfig, reg: Registries, *, fake: bool) -> None:
    """실행 전 전수 검사 — 자산 파일까지 본다. 30번째 셀에서 파일이 없어 죽으면
    이미 29장치 요금이 나간 뒤다."""
    problems: list[str] = []

    for key in config.models:
        spec = reg.models.get(key)
        if spec is None:
            problems.append(f"model {key!r} is not in configs/models.yaml")
            continue
        if not spec.enabled:
            problems.append(f"model {key!r} is enabled:false in models.yaml")
        if not fake and (not spec.model_id or spec.model_id.startswith("<")):
            problems.append(
                f"model {key!r}: model_id is still a placeholder "
                f"({spec.model_id!r}) — R0에서 최신 모델 ID를 확인해 기입 (05 §3 R0)"
            )

    for key in config.selfies:
        spec = reg.selfies.get(key)
        if spec is None:
            problems.append(f"selfie {key!r} is not in configs/selfies.yaml")
        elif not resolve(spec.file).exists():
            problems.append(f"selfie {key!r}: file not found — {spec.file}")

    for sku in config.products:
        spec = reg.products.get(sku)
        if spec is None:
            problems.append(f"product {sku!r} is not in configs/products.yaml")
            continue
        for ref in spec.ref_images:
            if not resolve(ref).exists():
                problems.append(f"product {sku}: ref image not found — {ref}")

    for pv in config.prompt_versions:
        if pv not in prompts.TEMPLATES:
            problems.append(
                f"prompt version {pv!r} is not in prompts.TEMPLATES "
                f"(known: {', '.join(prompts.known_versions())})"
            )

    if problems:
        raise ConfigError(
            "config validation failed:\n  - " + "\n  - ".join(problems)
        )


def resolve(path_str: str) -> Path:
    """설정 안의 상대 경로는 항상 research/ 기준이다 (cwd 기준이 아니다)."""
    path = Path(path_str)
    return path if path.is_absolute() else RESEARCH_ROOT / path


# --- 견적 --------------------------------------------------------------------


@dataclass
class Estimate:
    per_model: dict[str, int]
    per_model_cost: dict[str, float]
    total_cells: int
    total_cost: float


def estimate(cells: Iterable[Cell], reg: Registries) -> Estimate:
    per_model: dict[str, int] = {}
    per_model_cost: dict[str, float] = {}
    for cell in cells:
        per_model[cell.model] = per_model.get(cell.model, 0) + 1
    for model, count in per_model.items():
        unit = reg.models[model].cost_per_image_usd if model in reg.models else 0.0
        per_model_cost[model] = round(count * unit, 4)
    return Estimate(
        per_model=per_model,
        per_model_cost=per_model_cost,
        total_cells=sum(per_model.values()),
        total_cost=round(sum(per_model_cost.values()), 4),
    )


# --- 실행 --------------------------------------------------------------------


@dataclass
class RunOptions:
    dry_run: bool = False
    resume: bool = False
    restart: bool = False
    fake: bool = False
    only: dict[str, set[str]] = field(default_factory=dict)


@dataclass
class RunSummary:
    exp_dir: Path
    planned: int
    skipped: int
    attempted: int
    ok: int
    refused: int
    error: int
    timeout: int
    spent_usd: float
    budget_stopped: bool = False


_ONLY_FIELDS = {
    "model": "model",
    "selfie": "selfie",
    "product": "product",
    "sku": "product",
    "pv": "prompt_version",
    "prompt_version": "prompt_version",
    "rep": "rep",
}


def parse_only(items: Iterable[str]) -> dict[str, set[str]]:
    """--only model=gemini_nb --only rep=1 → {'model': {...}, 'rep': {...}}.

    같은 키는 OR, 다른 키는 AND.
    """
    filters: dict[str, set[str]] = {}
    for item in items:
        if "=" not in item:
            raise ConfigError(f"--only expects key=value (got {item!r})")
        key, value = item.split("=", 1)
        key = key.strip().lower()
        if key not in _ONLY_FIELDS:
            raise ConfigError(
                f"--only key {key!r} unknown; allowed: {sorted(set(_ONLY_FIELDS))}"
            )
        filters.setdefault(_ONLY_FIELDS[key], set()).add(value.strip())
    return filters


def apply_only(cells: list[Cell], filters: dict[str, set[str]]) -> list[Cell]:
    if not filters:
        return cells
    kept = []
    for cell in cells:
        if all(str(getattr(cell, attr)) in values for attr, values in filters.items()):
            kept.append(cell)
    return kept


def experiment_dir(exp_id: str, *, fake: bool) -> Path:
    return EXPERIMENTS_DIR / (exp_id + FAKE_SUFFIX if fake else exp_id)


def completed_cells(exp_dir: Path) -> set[str]:
    """성공 레코드 + 이미지 파일이 **둘 다** 있어야 완료다 (06 §5-5).
    manifest만 보고 스킵하면, 저장 직전에 죽은 셀이 영원히 빈 칸으로 남는다."""
    done: set[str] = set()
    for cell_id, row in manifest.latest_by_cell(
        manifest.read(exp_dir / manifest.MANIFEST_NAME)
    ).items():
        if row.get("status") != "ok":
            continue
        image_path = row.get("image_path")
        if image_path and (exp_dir / image_path).exists():
            done.add(cell_id)
    return done


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _AssetCache:
    """같은 셀카·상품컷을 셀마다 다시 읽지 않는다."""

    def __init__(self) -> None:
        self._data: dict[Path, bytes] = {}

    def get(self, path: Path) -> bytes:
        cached = self._data.get(path)
        if cached is None:
            cached = path.read_bytes()
            self._data[path] = cached
        return cached


async def execute(
    config: ExperimentConfig,
    reg: Registries,
    cells: list[Cell],
    opts: RunOptions,
    *,
    log=print,
) -> RunSummary:
    exp_dir = experiment_dir(config.exp_id, fake=opts.fake)
    images_dir = exp_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = exp_dir / manifest.MANIFEST_NAME

    done = completed_cells(exp_dir) if opts.resume else set()
    todo = [cell for cell in cells if cell.cell_id not in done]

    summary = RunSummary(
        exp_dir=exp_dir,
        planned=len(cells),
        skipped=len(cells) - len(todo),
        attempted=0,
        ok=0,
        refused=0,
        error=0,
        timeout=0,
        spent_usd=0.0,
    )
    if not todo:
        log("nothing to do — every planned cell is already complete.")
        return summary

    clients = {
        model: providers.load("fake" if opts.fake else reg.models[model].adapter)
        for model in {cell.model for cell in todo}
    }
    global_sem = asyncio.Semaphore(max(1, config.concurrency_global))
    model_sems = {
        model: asyncio.Semaphore(max(1, config.concurrency_per_model))
        for model in clients
    }
    assets = _AssetCache()
    total = len(todo)
    counter = {"n": 0}

    async def run_cell(cell: Cell) -> None:
        model = reg.models[cell.model]
        product = reg.products[cell.product]
        selfie = reg.selfies[cell.selfie]
        unit_cost = 0.0 if opts.fake else model.cost_per_image_usd

        prompt_text = prompts.assemble(
            product.contract_dict(), config.style_hints, cell.prompt_version
        )
        refs = [assets.get(resolve(ref)) for ref in product.ref_images]
        # R3 축① (ref 이미지 장수)용 노브 — models.yaml params로만 조절한다.
        max_refs = model.params.get("max_ref_images")
        if isinstance(max_refs, int) and max_refs > 0:
            refs = refs[:max_refs]
        call = GenCall(
            prompt=prompt_text,
            user_photo=assets.get(resolve(selfie.file)),
            product_refs=refs,
            model_id=model.model_id,
            params={k: v for k, v in model.params.items() if k != "max_ref_images"},
            timeout_s=config.timeout_s,
        )

        async with global_sem, model_sems[cell.model]:
            for attempt in (1, 2):
                # 예산 가드: 남은 캡을 넘길 시도는 아예 하지 않는다 (05 §6).
                if (
                    config.budget_cap_usd > 0
                    and summary.spent_usd + unit_cost > config.budget_cap_usd
                ):
                    summary.budget_stopped = True
                    return

                outcome = await _call_with_timeout(clients[cell.model], call)
                summary.attempted += 1
                summary.spent_usd = round(summary.spent_usd + unit_cost, 4)

                image_path = None
                if outcome.status == "ok" and outcome.image:
                    ext = sniff_image_ext(outcome.image)
                    target = images_dir / f"{cell.cell_id}.{ext}"
                    target.write_bytes(outcome.image)
                    image_path = f"images/{target.name}"

                manifest.append(
                    manifest_path,
                    manifest.Record(
                        ts=_now(),
                        exp_id=config.exp_id,
                        cell_id=cell.cell_id,
                        model=cell.model,
                        model_id="fake" if opts.fake else model.model_id,
                        selfie=cell.selfie,
                        product=cell.product,
                        wear_position=product.wear_position,
                        prompt_version=cell.prompt_version,
                        rep=cell.rep,
                        prompt_text=prompt_text,
                        status=outcome.status,
                        image_path=image_path,
                        latency_ms=outcome.latency_ms,
                        est_cost_usd=unit_cost,
                        error_message=outcome.error_message,
                        provider_meta=outcome.provider_meta,
                        attempt=attempt,
                    ),
                )

                if attempt == 1:
                    counter["n"] += 1  # 진행률은 셀 기준 — 재시도로 분모를 넘지 않게
                log(
                    f"[{counter['n']:>3}/{total}] {outcome.status:<7} "
                    f"{cell.cell_id}{' (retry)' if attempt > 1 else ''} "
                    f"{outcome.latency_ms}ms"
                    + (f" — {outcome.error_message}" if outcome.error_message else "")
                )
                setattr(summary, outcome.status, getattr(summary, outcome.status) + 1)

                if outcome.status not in _RETRYABLE or attempt == 2:
                    return
                await asyncio.sleep(_RETRY_BACKOFF_S)

    await asyncio.gather(*(run_cell(cell) for cell in todo))
    return summary


async def _call_with_timeout(client, call: GenCall) -> GenOutcome:
    """어댑터가 자체 타임아웃을 지키지 못해도 러너가 끊는다 (+5s 여유).
    어댑터 예외는 여기서 error로 정규화한다 — 셀 하나가 실행 전체를 죽이면 안 된다."""
    watch = Stopwatch()
    try:
        return await asyncio.wait_for(client.generate(call), timeout=call.timeout_s + 5)
    except asyncio.TimeoutError:
        return GenOutcome.failed("timeout", watch.ms(), "hard timeout in runner")
    except Exception as exc:  # noqa: BLE001 — 어떤 SDK 예외든 데이터로 남긴다
        return GenOutcome.failed("error", watch.ms(), f"{type(exc).__name__}: {exc}")


# --- 오케스트레이션 (CLI 진입점) ----------------------------------------------


def format_estimate(est: Estimate, config: ExperimentConfig, label: str) -> str:
    lines = [f"{label}: {est.total_cells} images"]
    for model in sorted(est.per_model):
        lines.append(
            f"  {model:<16} {est.per_model[model]:>4} images  "
            f"≈ ${est.per_model_cost[model]:.2f}"
        )
    cap = (
        f" / cap ${config.budget_cap_usd:.2f}" if config.budget_cap_usd > 0 else " / cap unset"
    )
    lines.append(f"  {'TOTAL':<16} {est.total_cells:>4} images  ≈ ${est.total_cost:.2f}{cap}")
    return "\n".join(lines)


NETWORK_PROBE_URL = "https://www.google.com/generate_204"
"""본문이 없는 204 응답 — 대역폭이 아니라 **회선 왕복 자체**를 잰다."""

NETWORK_WARN_MS = 800
"""이보다 느리면 경고. 정상은 20~50ms다.

근거(부록 A-10 ⓪): 회선이 열화된 상태에서 실험을 돌려 timeout 7건·연결오류 5건을 내고,
그 지연 측정치를 **프로바이더 부하로 오인해 결론까지 썼다.** 어댑터의 `latency_ms`는
입력 ~600KB 업로드 + 결과 ~300KB 다운로드를 포함한 벽시계라, 회선이 나쁘면 그대로 오염된다.
생성 지연을 재는 실험에서 이 구별이 없으면 측정 자체가 성립하지 않는다."""


def probe_network(*, log=print) -> Optional[float]:
    """생성 전 회선 왕복 점검. **막지는 않는다** — 판단은 사람이 한다.

    실패해도 조용히 넘어간다: 프로브 자체가 실험을 막아서는 안 되고, 오프라인
    환경에서 `--fake`를 돌리는 경우도 있다.
    """
    try:
        import urllib.request

        started = datetime.now(timezone.utc)
        urllib.request.urlopen(NETWORK_PROBE_URL, timeout=5).read()
        rtt_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
    except Exception:  # noqa: BLE001 — 프로브 실패는 데이터일 뿐 오류가 아니다
        log("!! 네트워크 사전점검 실패 — 회선을 확인하라 (지연 측정이 무의미해진다)")
        return None

    if rtt_ms > NETWORK_WARN_MS:
        log(
            f"!! 네트워크 사전점검: 본문 없는 204 응답에 {rtt_ms:.0f}ms "
            f"(정상 20~50ms). **회선이 열화됐다.**\n"
            "   이 상태로 돌리면 latency_ms가 생성 시간이 아니라 회선 상태를 잰다 "
            "— 지연 판정은 폐기해야 하고 timeout이 다발한다 (부록 A-10 ⓪).\n"
            "   품질 채점만 목적이면 진행해도 되지만, 지연이 산출물이면 중단하라."
        )
    return rtt_ms


def run(config_path: Path, opts: RunOptions, *, log=print) -> Optional[RunSummary]:
    config = load_experiment(config_path)
    reg = load_registries()
    validate(config, reg, fake=opts.fake)
    if not opts.fake:
        probe_network(log=log)

    cells = apply_only(expand(config), opts.only)
    if not cells:
        raise ConfigError("--only filtered out every cell; nothing to run")

    exp_dir = experiment_dir(config.exp_id, fake=opts.fake)
    manifest_path = exp_dir / manifest.MANIFEST_NAME
    done = completed_cells(exp_dir) if (opts.resume or manifest_path.exists()) else set()
    remaining = [cell for cell in cells if cell.cell_id not in done]

    log(f"experiment: {config.exp_id}{FAKE_SUFFIX if opts.fake else ''}  ({config.source})")
    log(format_estimate(estimate(cells, reg), config, "planned"))
    if done:
        log(
            format_estimate(
                estimate(remaining, reg), config, f"remaining (resume skips {len(done)})"
            )
        )

    if opts.fake:
        # 견적은 실프로바이더 단가로 보여 주되(그게 dry-run의 쓸모다) 캡으로 막지는
        # 않는다 — fake 실행은 실제로 0달러다.
        log("--fake: 실제 과금 없음 (위 견적은 실프로바이더 기준 참고값)")

    # 캡 판정은 '지금 실제로 돈이 나갈 분량'(= 남은 셀) 기준이다.
    est_to_spend = estimate(remaining if opts.resume else cells, reg)
    if not opts.fake and config.budget_cap_usd > 0 and est_to_spend.total_cost > config.budget_cap_usd:
        raise BudgetExceeded(
            f"estimate ${est_to_spend.total_cost:.2f} exceeds budget_cap_usd "
            f"${config.budget_cap_usd:.2f} — 실행을 거부한다. config를 줄이거나 "
            "--only로 쪼개거나 cap을 의식적으로 올려라 (05 §6)."
        )

    if opts.dry_run:
        log("dry-run — nothing was generated.")
        return None

    if manifest_path.exists() and not (opts.resume or opts.restart):
        # 이중 지출 방지. append-only manifest에 중복 행이 쌓이는 것도 막는다.
        raise ConfigError(
            f"{manifest_path} already exists ({len(done)} completed cells). "
            "--resume (완료분 스킵) 또는 --restart (전량 재생성, 과금 주의) 중 하나를 "
            "명시하라."
        )

    summary = asyncio.run(execute(config, reg, cells, opts, log=log))
    log(
        f"done: ok={summary.ok} refused={summary.refused} error={summary.error} "
        f"timeout={summary.timeout} skipped={summary.skipped} "
        f"attempts={summary.attempted} ≈ ${summary.spent_usd:.2f}"
    )
    if summary.budget_stopped:
        log(
            f"!! budget cap ${config.budget_cap_usd:.2f} reached — 남은 셀은 실행하지 "
            "않았다. --resume으로 이어서 돌릴 수 있다."
        )
    log(f"manifest: {summary.exp_dir / manifest.MANIFEST_NAME}")
    return summary
