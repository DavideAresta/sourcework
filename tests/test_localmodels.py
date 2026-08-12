"""Model discovery: what is on disk, and what it takes to serve it.

Everything here runs against a fake tree of empty files - the point is the
pairing, the ids and the offload decision, none of which need a real GGUF.
"""

from __future__ import annotations

from pathlib import Path

from sourcework.localmodels import LocalModel, discover, slugify, swap_config

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
