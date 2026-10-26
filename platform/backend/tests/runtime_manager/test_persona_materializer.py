import json
import hashlib
import stat
import pytest

from modules.runtime_manager.persona import PersonaArtifactMaterializer, PersonaArtifactSnapshot


def test_persona_artifact_is_read_only_and_reconstructable(tmp_path):
    artifact = PersonaArtifactSnapshot(
        agentee_id="agentee-1",
        persona_revision="rev-7",
        content_hash="sha256:" + hashlib.sha256(b"Be kind.").hexdigest(),
        generated_at="2026-07-13T00:00:00Z",
        instruction_payload="Be kind.",
        source_refs=("persona:agentee-1:rev-7",),
    )
    materializer = PersonaArtifactMaterializer(tmp_path / "allowed")
    first = materializer.materialize("runtime-1", 1, artifact)
    assert stat.S_IMODE(first.stat().st_mode) == 0o444
    assert json.loads(first.read_text())["persona_revision"] == "rev-7"

    replaced = materializer.materialize("runtime-1", 1, artifact)
    assert stat.S_IMODE(replaced.stat().st_mode) == 0o444

    first.chmod(0o644)
    first.unlink()
    rebuilt = materializer.materialize("runtime-1", 1, artifact)
    assert json.loads(rebuilt.read_text())["content_hash"] == artifact.content_hash


def test_persona_rejects_escape_symlink_and_hash_mismatch(tmp_path):
    artifact = PersonaArtifactSnapshot("a", "r", "sha256:bad", "now", "payload")
    materializer = PersonaArtifactMaterializer(tmp_path / "allowed")
    with pytest.raises(ValueError, match="runtime_id"):
        materializer.materialize("../escape", 1, artifact)
    with pytest.raises(ValueError, match="content_hash"):
        materializer.materialize("runtime-1", 1, artifact)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "allowed" / "runtime-link").symlink_to(outside, target_is_directory=True)
    valid = PersonaArtifactSnapshot(
        "a", "r", hashlib.sha256(b"payload").hexdigest(), "now", "payload"
    )
    with pytest.raises(ValueError, match="symlink"):
        materializer.materialize("runtime-link", 1, valid)
