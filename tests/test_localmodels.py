"""Model discovery: what is on disk, and what it takes to serve it.

Everything here runs against a fake tree of empty files - the point is the
pairing, the ids and the offload decision, none of which need a real GGUF.
"""

from __future__ import annotations

from pathlib import Path

from sourcework.localmodels import (
    REQUIRED_FLAGS,
    LocalModel,
    discover,
    model_dirs,
    slugify,
    swap_config,
)

SERVER = "llama-server --port ${PORT}"


def make(path: Path, size: int = 1024) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


def test_the_quant_leaves_the_id_but_the_model_name_stays():
    # Re-quantising a model must not rename it out from under a saved config.
    assert slugify("Qwen3.5-9B-Q8_0.gguf") == "qwen3.5-9b"
    assert slugify("gpt-oss-20b-MXFP4.gguf") == "gpt-oss-20b"
    assert slugify("gemma-4-12B-it-Q4_K_M.gguf") == "gemma-4-12b-it"
    # `-it` and `-instruct` are load-bearing: they distinguish real models.
    assert slugify("Foo-7B-Instruct-Q4_K_M.gguf") != slugify("Foo-7B-Q4_K_M.gguf")


def test_a_projector_is_paired_not_listed(tmp_path: Path):
    """A projector served as a model is a model that cannot load; a projector
    *missed* is a vision model that answers about nothing."""
    make(tmp_path / "Qwen3.5-9B-GGUF" / "Qwen3.5-9B-Q8_0.gguf")
    make(tmp_path / "Qwen3.5-9B-GGUF" / "mmproj-Qwen3.5-9B-BF16.gguf")

    models, warnings = discover([tmp_path])

    assert [m.id for m in models] == ["qwen3.5-9b"]
    assert models[0].vision is True
    assert models[0].mmproj is not None
    assert warnings == []


def test_a_split_model_is_one_model_not_six(tmp_path: Path):
    for part in range(1, 4):
        make(tmp_path / "Big" / f"Big-Q4_K_M-{part:05d}-of-00003.gguf", size=2048)

    models, _ = discover([tmp_path])

    assert len(models) == 1, "llama.cpp opens the later shards itself"
    assert models[0].path.name.endswith("-00001-of-00003.gguf")
    # Size is the whole model, or the offload decision is made on a third of it.
    assert models[0].size_bytes == 3 * 2048


def test_a_shadowed_duplicate_is_reported_rather_than_dropped(tmp_path: Path):
    first, second = tmp_path / "a", tmp_path / "b"
    make(first / "Qwen3.5-9B-Q8_0.gguf")
    make(second / "Qwen3.5-9B-Q8_0.gguf")

    models, warnings = discover([first, second])

    assert len(models) == 1
    assert models[0].path.is_relative_to(first), "the first directory wins"
    assert any("ignoring" in w and str(second) in w for w in warnings)


def test_a_missing_directory_is_said_out_loud(tmp_path: Path):
    _, warnings = discover([tmp_path / "nope"])
    assert any("not a directory" in w for w in warnings)


def test_full_offload_is_only_promised_when_it_actually_fits():
    """--fit cannot rescue an argument that was set explicitly, so `-ngl 99` on
    a model too big for the card is an out-of-memory crash, not a slow run."""
    small = LocalModel(id="small", path=Path("/m/small.gguf"), size_bytes=6 * 1024**3)
    big = LocalModel(id="big", path=Path("/m/big.gguf"), size_bytes=15 * 1024**3)

    config = swap_config([small, big], server=SERVER, vram_gb=16.0)

    small_block = config.split("  small:")[1].split("  big:")[0]
    big_block = config.split("  big:")[1]
    assert "-ngl 99" in small_block
    assert "-ngl 99" not in big_block
    assert "--fit" in big_block, "the reader has to know why this one is different"


def test_a_hugging_face_entry_serves_without_a_local_file():
    remote = LocalModel(id="gemma", hf_repo="unsloth/gemma-3-27b-it-GGUF:Q4_K_M")

    config = swap_config([remote], server=SERVER, vram_gb=16.0)

    assert "-hf unsloth/gemma-3-27b-it-GGUF:Q4_K_M" in config
    assert "-m " not in config
    # An unknown size must not be read as "fits comfortably".
    assert "-ngl 99" not in config


def test_the_projector_reaches_the_generated_command(tmp_path: Path):
    make(tmp_path / "V" / "Vision-7B-Q4_K_M.gguf")
    make(tmp_path / "V" / "mmproj-Vision-7B-F16.gguf")

    models, _ = discover([tmp_path])
    config = swap_config(models, server=SERVER, vram_gb=16.0)

    assert "--mmproj" in config and "mmproj-Vision-7B-F16.gguf" in config


# ---------------------------------------------------------------------------
# The generated command has to be one the binary will actually accept
# ---------------------------------------------------------------------------


def test_the_generated_command_uses_flags_every_build_accepts():
    """`-rea off` was a short form only newer llama.cpp builds have.

    The generator resolves llama-server from PATH, so on a machine with an older
    build first it wrote a command that binary rejects outright: llama-server
    exited in a quarter of a second and llama-swap reported "upstream command
    exited prematurely". Every check above it stayed green, because the endpoint
    was up and the model list was real - nothing failed until a model was
    actually requested, which is the one thing a probe cannot do.

    `--reasoning-budget` is the long form, is accepted by every build that has
    the feature, and says the thing actually meant: `-rea` is an alias for
    `--reasoning-format`, which decides where thoughts go, not whether they
    happen.
    """
    config = swap_config(
        [LocalModel(id="qwen3.8-27b", path=Path("/models/q.gguf"), size_bytes=int(15.7 * 2**30))],
        server="llama-server",
        vram_gb=16.0,
    )
    assert "--reasoning-budget 0" in config
    assert "-rea " not in config
    # Every flag the config relies on must be one a caller can check for.
    assert all(flag in config for flag in REQUIRED_FLAGS)


def test_the_model_directories_are_read_from_the_env_file_too(tmp_path: Path, monkeypatch):
    """`.env.example` has documented SOURCEWORK_MODEL_DIRS all along.

    Reading only os.environ made it the one documented setting that did not work
    from where the documentation puts it - a value in `.env` was invisible
    unless something in the process had already called `load_dotenv`. Importing
    litellm does, which is exactly why this worked from inside the app and not
    from `scripts/llama-models.py`.
    """
    from sourcework.config import Settings, settings

    monkeypatch.delenv("SOURCEWORK_MODEL_DIRS", raising=False)
    env = tmp_path / ".env"
    env.write_text(f"SOURCEWORK_MODEL_DIRS={tmp_path}/a:{tmp_path}/b\n", encoding="utf-8")

    settings.cache_clear()
    monkeypatch.setattr(
        "sourcework.config.settings",
        lambda: Settings(_env_file=str(env)),  # type: ignore[call-arg]
    )
    assert model_dirs() == [tmp_path / "a", tmp_path / "b"]

    # An explicit environment value still wins over the file.
    monkeypatch.setenv("SOURCEWORK_MODEL_DIRS", str(tmp_path / "c"))
    assert model_dirs() == [tmp_path / "c"]
    settings.cache_clear()


def _script():
    """`scripts/llama-models.py` as a module. Its name is not an identifier."""
    import importlib.util

    path = Path(__file__).parent.parent / "scripts" / "llama-models.py"
    spec = importlib.util.spec_from_file_location("llama_models_script", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_hand_written_server_macro_wins_over_whatever_is_on_the_path(tmp_path: Path):
    """The macro is where somebody wrote down which llama.cpp build to run.

    The generator used to ignore it and resolve `llama-server` from PATH, so a
    machine with two builds installed got generated entries pointing at one and
    hand-tuned entries pointing at the other. That is how a flag the curated
    build accepts ended up in front of a build that rejects it, and the config
    became unusable without anything reporting a problem.

    llama-swap macros do not cross config files, so the generated entries cannot
    write `${server}` and inherit it - the command has to be inlined, which is
    precisely why it has to be read from here rather than reinvented.
    """
    script = _script()
    config = tmp_path / "llama-swap.yaml"
    config.write_text(
        "healthCheckTimeout: 300\n"
        "\n"
        "macros:\n"
        "  server: >\n"
        "    /usr/bin/env LD_LIBRARY_PATH=/opt/llama-b10369\n"
        "    /opt/llama-b10369/llama-server\n"
        "    --host 127.0.0.1 --port ${PORT} --jinja\n"
        "  lmstudio: /home/me/models\n"
        "\n"
        "models:\n"
        "  gpt-oss-20b:\n"
        "    cmd: ${server}\n",
        encoding="utf-8",
    )
    found = script.curated_server(config)
    assert found is not None
    # Folded to one line, and carrying the build the macro actually names.
    assert "/opt/llama-b10369/llama-server" in found
    assert "${PORT}" in found
    assert "\n" not in found
    # The macro that follows it must not be swallowed into the folded scalar.
    assert "lmstudio" not in found


def test_a_config_with_no_server_macro_falls_back_rather_than_failing(tmp_path: Path):
    """Not every config defines one, and the generator still has to work."""
    script = _script()
    bare = tmp_path / "llama-swap.yaml"
    bare.write_text("healthCheckTimeout: 300\n\nmodels:\n  a:\n    cmd: x\n", encoding="utf-8")
    assert script.curated_server(bare) is None
    assert script.curated_server(tmp_path / "does-not-exist.yaml") is None
