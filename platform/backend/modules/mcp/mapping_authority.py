"""Stable semantic authority for canonical-to-wire mapping evidence.

Golden vectors are primary.  A normalized Python AST digest of the two
controlled mapper sources is supplemental coverage for branches not expressed
by the positive vectors; comments and formatting do not change that digest.
"""
from __future__ import annotations

import ast
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[4]
VECTOR_PATH = Path(__file__).with_name("evidence") / "canonical-mapping-vectors.json"
MAPPER_SOURCE_PATHS = (
    Path("agent/protocol/canonical_surface.py"),
    Path("agent/protocol/tool_mapper.py"),
)


def _sha256(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def load_mapping_vectors(path: Path = VECTOR_PATH) -> Mapping[str, Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "starcoplay.canonical-mapping-vectors.v1":
        raise ValueError("unsupported canonical mapping vector schema")
    vectors = payload.get("vectors")
    if not isinstance(vectors, dict) or not vectors:
        raise ValueError("canonical mapping vectors must be a non-empty object")
    return vectors


def semantic_vector_digest(path: Path = VECTOR_PATH) -> str:
    # Parsed/canonical JSON makes whitespace and key ordering non-semantic.
    canonical = json.dumps(
        load_mapping_vectors(path), sort_keys=True, separators=(",", ":"),
    ).encode()
    return _sha256(canonical)


def mapper_source_digest(
    repo_root: Path = REPO_ROOT,
    source_paths: tuple[Path, ...] = MAPPER_SOURCE_PATHS,
) -> str:
    normalized = []
    for relative in source_paths:
        source = (repo_root / relative).read_text(encoding="utf-8")
        normalized.append({
            "path": relative.as_posix(),
            "python_ast": ast.dump(ast.parse(source), include_attributes=False),
        })
    return _sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode())


def mapper_authority(repo_root: Path = REPO_ROOT) -> Mapping[str, str]:
    return {
        "algorithm": "golden-json-v1+python-ast-v1",
        "semantic_vector_digest": semantic_vector_digest(),
        "controlled_source_digest": mapper_source_digest(repo_root),
    }


def expected_mapping(name: str, command_id: str) -> Mapping[str, Any]:
    expected = deepcopy(load_mapping_vectors()[name]["expected"])

    def replace(value):
        if value == "$COMMAND_ID":
            return command_id
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value

    return replace(expected)
