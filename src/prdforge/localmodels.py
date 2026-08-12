"""Which local models exist, and how to serve them.

PRD Forge addresses models by id and reaches them over one OpenAI-compatible
endpoint. Something has to turn "a folder of GGUF files" into that endpoint's
configuration, and doing it by hand is how a model ends up unreachable because
its projector was never paired or its id was typed differently in two places.

Three decisions worth knowing:

* **Directories are configuration, not code.** ``PRDFORGE_MODEL_DIRS`` lists
  where to look, so adding a model is dropping a file in a folder rather than
  editing a YAML by hand.

* **Downloads are llama.cpp's job.** ``llama-server -hf <repo>:<quant>`` already
  fetches from Hugging Face, resumes, caches, and honours ``HF_TOKEN``. A
  second downloader here would be a worse copy of it, so a remote model is
  recorded as an ``-hf`` entry and arrives on first use.

* **Offload is decided per model.** ``--fit`` only adjusts arguments that were
  *not* set, so pinning ``-ngl 99`` on a model too big for the card turns a
  slow run into an out-of-memory crash. Anything that comfortably fits asks for
  full offload; anything that does not is left for llama.cpp to place.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CTX = 32768

# What a KV cache plus compute buffers cost on top of the weights. Rough, and
# deliberately generous: being wrong in the optimistic direction is an OOM at
# load time, being wrong the other way costs a few layers on the CPU.
OVERHEAD_GB = 2.5

_MULTIPART = re.compile(r"-(\d{5})-of-(\d{5})\.gguf$", re.IGNORECASE)
_YAML_MODEL_ID = re.compile(r"^  ([A-Za-z0-9][\w.-]*):\s*$")
_QUANT = re.compile(
    r"[-.](Q\d[_A-Z0-9]*|IQ\d[_A-Z0-9]*|BF16|F16|F32|MXFP4)(?:[-.]|$)", re.IGNORECASE
)


@dataclass
class LocalModel:
    """One servable model on disk, or one not here yet."""

    id: str
    path: Path | None = None
    """None for a Hugging Face entry that has not been fetched."""

    hf_repo: str | None = None
    mmproj: Path | None = None
    """Vision projector. Without it a multimodal model silently answers about
    nothing when handed an image, which is worse than refusing."""

    size_bytes: int = 0
    extra_args: list[str] = field(default_factory=list)

    @property
    def size_gb(self) -> float:
        return self.size_bytes / 1024**3

    @property
    def vision(self) -> bool:
        return self.mmproj is not None

    def fits(self, vram_gb: float) -> bool:
        """Can the whole thing sit on the card with room for its KV cache?"""
        return bool(self.size_bytes) and self.size_gb + OVERHEAD_GB <= vram_gb


def slugify(name: str) -> str:
    """A filename into a model id someone would willingly type.

    Only the quant suffix goes: ``qwen3.5-9b`` is the model, ``Q8_0`` is how it
    was packed, and putting the packing in the id means re-quantising later
    silently breaks every config that named it.

    ``-it`` and ``-instruct`` stay, tempting as they are to drop. They are the
    difference between two real models often sitting in the same collection,
    and an id that collides is a model you cannot address.
    """
    stem = _MULTIPART.sub("", name)
    stem = stem.removesuffix(".gguf")
    stem = _QUANT.sub("-", stem)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
    return stem.lower() or "model"


def model_dirs() -> list[Path]:
    """Where to look for GGUFs: ``PRDFORGE_MODEL_DIRS``, colon-separated."""
    raw = os.environ.get("PRDFORGE_MODEL_DIRS", "")
    return [Path(p).expanduser() for p in raw.split(":") if p.strip()]


def discover(roots: list[Path]) -> tuple[list[LocalModel], list[str]]:
    """Every servable GGUF under ``roots``, projectors paired to their model.

    Returns the models and any warnings. A multi-part model is reported once,
    under its first shard - llama.cpp opens the rest itself, and listing all
    six parts as six models is how you get a picker full of things that cannot
    load.

    Two files that reduce to the same id are a real possibility across
    collections, and the shadowed one has to be *said* rather than quietly
    dropped: a model that vanished from the list with no explanation is
    indistinguishable from one that was never there.
    """
    found: dict[str, LocalModel] = {}
    warnings: list[str] = []

    for root in roots:
        if not root.is_dir():
            warnings.append(f"not a directory, skipped: {root}")
            continue
        for path in sorted(root.rglob("*.gguf")):
            name = path.name
            if name.lower().startswith("mmproj"):
                continue  # a projector is an attribute of a model, not a model
            part = _MULTIPART.search(name)
            if part and part.group(1) != "00001":
                continue  # later shards belong to the first one

            model_id = slugify(name)
            if model_id in found:
                # First root wins, so directory order expresses precedence.
                if found[model_id].path != path:
                    warnings.append(
                        f"{model_id!r}: using {found[model_id].path}, ignoring {path}"
                    )
                continue

            projector = next(
                (p for p in sorted(path.parent.glob("mmproj*.gguf"))),
                None,
            )
            found[model_id] = LocalModel(
                id=model_id,
                path=path,
                mmproj=projector,
                size_bytes=_total_size(path, part),
            )

    return sorted(found.values(), key=lambda m: m.id), warnings


def _total_size(path: Path, part: re.Match[str] | None) -> int:
    """Bytes on disk, counting every shard of a split model."""
    if not part:
        return path.stat().st_size
    pattern = path.name[: part.start()] + "-*-of-" + part.group(2) + ".gguf"
    return sum(p.stat().st_size for p in path.parent.glob(pattern))


def curated_ids(config: Path) -> set[str]:
    """Model ids already defined by hand in ``config``.

    A generated entry has no idea that gpt-oss needs its reasoning effort
    capped or that a particular model wants a smaller window. Where someone has
    written that down, the handwritten entry wins and the scan stays out of the
    way - otherwise every rescan would quietly undo the tuning that made a
    model work.
    """
    if not config.is_file():
        return set()
    ids: set[str] = set()
    in_models = False
    for line in config.read_text(encoding="utf-8").splitlines():
        if line.startswith("models:"):
            in_models = True
            continue
        if in_models and line and not line.startswith((" ", "#")):
            in_models = False
        if in_models:
            match = _YAML_MODEL_ID.match(line)
            if match:
                ids.add(match.group(1))
    return ids


def swap_config(
    models: list[LocalModel],
    *,
    server: str,
    vram_gb: float,
    ctx: int = DEFAULT_CTX,
    ttl: int = 900,
) -> str:
    """A llama-swap config serving ``models``, one process at a time.

    Generated rather than hand-written so that the id PRD Forge asks for and
    the file llama-server opens cannot drift apart.
    """
    lines = [
        "# Generated by prdforge-models. Edits here are lost on the next scan -",
        "# add a directory to PRDFORGE_MODEL_DIRS, or edit llama-swap.yaml instead.",
        "healthCheckTimeout: 300",
        "",
        "models:",
    ]

    for model in models:
        fitted = model.fits(vram_gb)
        # `-rea off` by default: a hybrid reasoning model spends the whole
        # output budget in its scratchpad and returns empty content, which the
        # pipeline can only report as "the backend said nothing". A model that
        # should think is a curated entry, not a discovered one.
        args = [f"-c {ctx}", "-rea off"]
        if fitted:
            # Only when it demonstrably fits: --fit cannot rescue an argument
            # that was set explicitly, so this is the line between "some layers
            # on the CPU" and an out-of-memory crash.
            args.append("-ngl 99")
        args += model.extra_args

        source = f"-hf {model.hf_repo}" if model.hf_repo else f"-m {model.path}"
        note = (
            f"  # {model.size_gb:.1f} GB, "
            + ("fits on the card" if fitted else "larger than VRAM - placement left to --fit")
            + (", vision" if model.vision else "")
            if model.path
            else "  # downloaded from Hugging Face on first use"
        )

        lines.append(f"  {model.id}:")
        lines.append(note)
        lines.append("    cmd: |")
        lines.append(f"      {server}")
        lines.append(f"      {source}")
        if model.mmproj:
            lines.append(f"      --mmproj {model.mmproj}")
        lines.append("      " + " ".join(args))
        lines.append(f"    ttl: {ttl}")
        lines.append("")

    return "\n".join(lines)
