import json
import stat

import pytest

from modules.mcp.core.errors import McpError
from modules.mcp.profiles.local_rendezvous import LocalRendezvous


def test_publish_is_atomic_owner_only_and_cleanup_is_idempotent(tmp_path):
    path = tmp_path / "owner" / "rendezvous.json"
    rendezvous = LocalRendezvous(path)
    rendezvous.publish({"proof": "secret", "endpoint": "ws://127.0.0.1:1"})
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["proof"] == "secret"
    assert "secret" not in repr(rendezvous)
    with pytest.raises(McpError) as caught:
        LocalRendezvous(path).publish({"proof": "attacker"})
    assert caught.value.code == "conflict"
    path.unlink()
    assert rendezvous.claimed()
    rendezvous.cleanup()


def test_cleanup_never_deletes_a_replacement_owner_after_claim(tmp_path):
    path = tmp_path / "owner" / "rendezvous.json"
    first = LocalRendezvous(path)
    first.publish({"proof": "first"})
    path.unlink()  # Mod atomically claimed the first owner's inode.
    second = LocalRendezvous(path)
    second.publish({"proof": "second"})
    first.cleanup()
    assert json.loads(path.read_text()) == {"proof": "second"}
    second.cleanup()
    assert not path.exists()
