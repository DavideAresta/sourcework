"""The two compose files, checked the way ``docker compose`` checks them.

Nothing else in the suite reads them, and both failures guarded below shipped in
`main`. One stopped ``docker compose`` parsing the file at all. The other was
silent: the mesh came up, every agent answered its healthcheck, and not one of
them could reach another.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sourcework.config import PeerSettings

COMPOSE = [
    Path(__file__).parent.parent / "docker-compose.yml",
    Path(__file__).parent.parent / "docker-compose.cloud.yml",
]

PEERS = PeerSettings().as_map()


class _StrictLoader(yaml.SafeLoader):
    """A loader that refuses a mapping key defined twice.

    PyYAML keeps the last one without complaining, which is exactly why the
    duplicate went unnoticed: every parser in the chain tolerated it until
    Compose v5, which rejects the file outright.
    """


MERGE_TAG = "tag:yaml.org,2002:merge"


def _no_duplicates(loader, node, deep=False):  # noqa: ANN001, ANN202
    # Scanned before delegating, because `construct_mapping` resolves `<<` by
    # appending the merged pairs to this node - after which a key that is
    # legitimately overriding an inherited one (every service's PUBLIC_HOST)
    # would read as a duplicate.
    seen: set[str] = set()
    for key_node, _ in node.value:
        if key_node.tag == MERGE_TAG:
            continue  # `<<` is an instruction, not a key, and may repeat
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ValueError(f"line {key_node.start_mark.line + 1}: {key!r} defined twice")
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates)


@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_no_block_defines_the_same_key_twice(path: Path):
    """A duplicate key is a rejected file, not a last-one-wins convenience.

    The `ui` service carried two `command:` lines - the second one added to make
    the container reachable, the first left behind. Older parsers took the last
    and the mistake stayed invisible; Compose v5 refuses to load the file, so
    `docker compose up` failed on a repo where nothing else had changed.
    """
    yaml.load(path.read_text(), Loader=_StrictLoader)


@pytest.mark.parametrize("path", COMPOSE, ids=lambda p: p.name)
def test_every_service_that_speaks_to_the_mesh_can_find_it(path: Path):
    """Each service must resolve all eight peers, at service names.

    A YAML merge key merges one level only, so a service that declares its own
    `environment` *replaces* the anchor's map rather than adding to it. Every
    service declares one - to set its own PUBLIC_HOST and PORT - so every one of
    them silently lost the peer URLs and fell back to the localhost defaults in
    `.env`. Inside a container localhost is the container, so the orchestrator
    could not reach a single specialist and the UI could not reach the
    orchestrator.
    """
    config = yaml.load(path.read_text(), Loader=_StrictLoader)

    checked = 0
    for name, service in config["services"].items():
        environment = service.get("environment") or {}
        # Postgres is not a mesh member and must not be handed mesh settings.
        if not any(key.startswith("SOURCEWORK_") for key in environment):
            continue
        checked += 1
        for peer, default_url in PEERS.items():
            key = f"SOURCEWORK_PEERS__{peer.upper()}"
            assert key in environment, f"{name} cannot reach {peer}"
            # The service name, not localhost - that is the whole point.
            assert environment[key] == default_url.replace("localhost", peer)

    assert checked >= len(PEERS), f"{path.name}: only {checked} service(s) carry mesh settings"
