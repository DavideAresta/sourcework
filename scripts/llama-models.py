#!/usr/bin/env python3
"""Find local models, and write the config that serves them.

    scripts/llama-models.py list
    scripts/llama-models.py scan
    scripts/llama-models.py add unsloth/gemma-3-27b-it-GGUF:Q4_K_M

`scan` writes `scripts/llama-swap.d/models.yaml`. Run llama-swap with
`-config-dir scripts/llama-swap.d -watch-config` and a rescan takes effect
without restarting anything.

Where it looks is `SOURCEWORK_MODEL_DIRS` (colon-separated), so adding a model is
dropping a file in a folder. `add` records a Hugging Face repo instead;
llama-server fetches it on first use, resuming and caching as it goes.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sourcework.localmodels import (  # noqa: E402
    DEFAULT_CTX,
    REQUIRED_FLAGS,
    LocalModel,
    curated_ids,
    discover,
    model_dirs,
    slugify,
    swap_config,
)

CURATED = ROOT / "scripts" / "llama-swap.yaml"
OUT = ROOT / "scripts" / "llama-swap.d" / "models.yaml"
EXTRA = ROOT / "scripts" / "llama-swap.d" / "downloaded.txt"


def llama_bin() -> Path:
    """Where llama-server lives: $LLAMA_BIN, else wherever PATH found it."""
    override = os.environ.get("LLAMA_BIN")
    if override:
        return Path(override).expanduser()
    found = shutil.which("llama-server")
    if found:
        return Path(found).resolve().parent
    sys.exit(
        "llama-server not found. Install llama.cpp and put it on PATH, or set "
        "LLAMA_BIN to the directory containing it.\n"
        "  https://github.com/ggml-org/llama.cpp/releases"
    )


def curated_server(config: Path) -> str | None:
    """The ``server`` macro from the hand-written config, if it defines one.

    llama-swap macros do not cross config files, so the generated entries cannot
    write ``${server}`` and inherit it - they have to inline the same command.
    Which is exactly why this has to be read: the macro is where somebody has
    already written down *which llama.cpp build to run*, and the generator used
    to ignore it and resolve `llama-server` from PATH instead. A machine with
    two builds installed then got generated entries pointing at one and
    hand-tuned entries pointing at the other, which is how a flag that the
    curated build accepts ended up in front of a build that rejects it.
    """
    if not config.is_file():
        return None
    lines = config.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("macros:"):
            continue
        for offset in range(index + 1, len(lines)):
            entry = lines[offset]
            if entry and not entry.startswith((" ", "#")):
                return None  # left the macros block without finding one
            match = re.match(r"^  server:\s*(.*)$", entry)
            if not match:
                continue
            inline = match.group(1).strip()
            if inline and inline not in (">", "|", ">-", "|-"):
                return inline
            # A block scalar: every following, more-indented line, folded to one.
            body: list[str] = []
            for tail in lines[offset + 1:]:
                if tail.strip() and not tail.startswith("    "):
                    break
                if tail.strip():
                    body.append(tail.strip())
            return " ".join(body) or None
    return None


def server_command() -> str:
    """The invocation every generated entry starts with.

    The curated macro wins when there is one, so generated and hand-tuned
    entries cannot disagree about which binary they run. Otherwise it is built
    from ``llama_bin()`` - LD_LIBRARY_PATH included, because prebuilt llama.cpp
    ships its shared objects next to the binary and will not find them
    otherwise.
    """
    curated = curated_server(CURATED)
    if curated:
        return curated
    binary = llama_bin()
    return (
        f"/usr/bin/env LD_LIBRARY_PATH={binary} {binary}/llama-server "
        "--host 127.0.0.1 --port ${PORT} --jinja --metrics"
    )


def check_flags(server: str) -> None:
    """Confirm the binary these entries will run accepts the flags they carry.

    Generating a command and never asking whether it can run is how the whole
    config became unusable over one short flag: llama-server rejected it and
    exited in a quarter of a second, and every check above it stayed green
    because the endpoint really was up and the model list really was real.
    Nothing failed until a model was requested.

    A warning rather than a refusal: this parses a command line to find the
    binary, and being wrong about that should not stop somebody generating a
    config that would have worked.
    """
    match = re.search(r"(\S*llama-server)", server)
    if not match:
        return
    binary = match.group(1)
    try:
        helped = subprocess.run(  # noqa: S603 - path comes from our own config
            [binary, "--help"], capture_output=True, text=True, timeout=30, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  ! could not ask {binary} which flags it takes: {exc}", file=sys.stderr)
        return
    missing = [flag for flag in REQUIRED_FLAGS if flag not in helped]
    if missing:
        print(
            f"  ! {binary} does not list {', '.join(missing)}. The generated config "
            "uses it, so llama-server will exit immediately and llama-swap will "
            "report 'upstream command exited prematurely'. Point LLAMA_BIN at a "
            "newer build, or set the `server` macro in llama-swap.yaml.",
            file=sys.stderr,
        )


def detect_vram_gb() -> float:
    """Usable VRAM on the first GPU. 0 when there is no tool to ask."""
    if shutil.which("rocm-smi"):
        out = subprocess.run(
            ["rocm-smi", "--showmeminfo", "vram"], capture_output=True, text=True
        ).stdout
        for line in out.splitlines():
            if "GPU[0]" in line and "Total Memory" in line:
                return int(line.split(":")[-1].strip()) / 1024**3
    if shutil.which("nvidia-smi"):
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True,
        ).stdout.strip().splitlines()
        if out:
            return int(out[0]) / 1024
    return 0.0


def remote_models() -> list[LocalModel]:
    """Hugging Face entries added with `add`, one `repo[:quant]` per line."""
    if not EXTRA.exists():
        return []
    out = []
    for line in EXTRA.read_text(encoding="utf-8").splitlines():
        spec = line.strip()
        if not spec or spec.startswith("#"):
            continue
        out.append(LocalModel(id=slugify(spec.split(":")[0].split("/")[-1]), hf_repo=spec))
    return out


def cmd_list(args: argparse.Namespace) -> int:
    roots = [Path(p).expanduser() for p in args.dir] or model_dirs()
    if not roots:
        print("No model directories. Set SOURCEWORK_MODEL_DIRS or pass --dir.", file=sys.stderr)
        return 2

    vram = args.vram or detect_vram_gb()
    models, warnings = discover(roots)
    print(f"{len(models)} model(s) across {len(roots)} director(ies); {vram:.1f} GB VRAM\n")
    for m in models:
        flags = []
        if m.vision:
            flags.append("vision")
        flags.append("fits" if m.fits(vram) else "needs CPU offload")
        print(f"  {m.id:28} {m.size_gb:6.1f} GB  {', '.join(flags)}")
    for m in remote_models():
        print(f"  {m.id:28} {'—':>6}     hugging face: {m.hf_repo}")
    for warning in warnings:
        print(f"\n  ! {warning}", file=sys.stderr)
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    roots = [Path(p).expanduser() for p in args.dir] or model_dirs()
    if not roots:
        print("No model directories. Set SOURCEWORK_MODEL_DIRS or pass --dir.", file=sys.stderr)
        return 2

    vram = args.vram or detect_vram_gb()
    discovered, warnings = discover(roots)
    curated = curated_ids(CURATED)
    models = [m for m in discovered + remote_models() if m.id not in curated]
    skipped = sorted(curated & {m.id for m in discovered + remote_models()})
    for warning in warnings:
        print(f"  ! {warning}", file=sys.stderr)
    if skipped:
        print(f"Tuned by hand in {CURATED.name}, left alone: {', '.join(skipped)}")
    if not models:
        print(f"No .gguf files under: {', '.join(str(r) for r in roots)}", file=sys.stderr)
        return 1

    server = server_command()
    check_flags(server)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        swap_config(models, server=server, vram_gb=vram, ctx=args.ctx),
        encoding="utf-8",
    )
    print(f"Wrote {OUT} — {len(models)} model(s), {vram:.1f} GB VRAM assumed.")
    print("llama-swap reloads it by itself when started with -watch-config.")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    EXTRA.parent.mkdir(parents=True, exist_ok=True)
    existing = EXTRA.read_text(encoding="utf-8").splitlines() if EXTRA.exists() else []
    if args.repo in existing:
        print(f"{args.repo} is already listed.")
    else:
        EXTRA.write_text("\n".join([*existing, args.repo]) + "\n", encoding="utf-8")
        print(f"Added {args.repo}.")
    print("It downloads on first use - llama-server resumes and caches it.")
    print("Set HF_TOKEN first for a gated repo.")
    return cmd_scan(args)


def main() -> int:
    parser = argparse.ArgumentParser(prog="llama-models.py", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dir", action="append", default=[],
                        help="a directory to scan (repeatable). Default: SOURCEWORK_MODEL_DIRS")
    parser.add_argument("--vram", type=float, help="GB of VRAM to assume. Default: detected")
    parser.add_argument("--ctx", type=int, default=DEFAULT_CTX)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="what is on disk").set_defaults(func=cmd_list)
    sub.add_parser("scan", help="regenerate the llama-swap config").set_defaults(func=cmd_scan)
    add = sub.add_parser("add", help="record a Hugging Face repo to fetch on first use")
    add.add_argument("repo", help="user/model[:quant], e.g. unsloth/gemma-3-27b-it-GGUF:Q4_K_M")
    add.set_defaults(func=cmd_add)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
