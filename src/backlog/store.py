"""Portable Backlog Store manifest and exact-root context loading."""

import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MANIFEST_FILENAME = "backlog.json"
ITEMS_DIRNAME = "items"
INDEX_FILENAME = "INDEX.md"
LOCK_FILENAME = ".lock"

_PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_ID_PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")


class StoreLoadError(ValueError):
    """Raised when an exact portable store cannot be loaded safely."""


class StoreManifest(BaseModel):
    """Strict manifest for one portable Backlog Store."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    store_schema: Literal["backlog/Store@1"] = Field(validation_alias="schema", serialization_alias="schema")
    project_id: str
    id_prefix: str

    @field_validator("project_id")
    @classmethod
    def validate_project_id(cls, value: str) -> str:
        if not _PROJECT_ID_PATTERN.fullmatch(value):
            raise ValueError("project_id must contain only letters, digits, hyphens, and underscores")
        return value

    @field_validator("id_prefix")
    @classmethod
    def validate_id_prefix(cls, value: str) -> str:
        if not _ID_PREFIX_PATTERN.fullmatch(value):
            raise ValueError("id_prefix must use uppercase letters, digits, and underscores and start with a letter")
        return value


@dataclass(frozen=True, slots=True)
class StoreContext:
    """Immutable exact-root authority for all portable store paths."""

    root: Path
    manifest: StoreManifest
    manifest_path: Path
    items_path: Path
    index_path: Path
    lock_path: Path


def _require_directory(path: Path, description: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise StoreLoadError(f"{description} is missing: {path}") from error
    except OSError as error:
        raise StoreLoadError(f"cannot inspect {description}: {path}") from error
    if not stat.S_ISDIR(mode):
        raise StoreLoadError(f"{description} must be a directory: {path}")
    return path


def _require_regular_file(path: Path, description: str) -> Path:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as error:
        raise StoreLoadError(f"{description} is missing: {path}") from error
    except OSError as error:
        raise StoreLoadError(f"cannot inspect {description}: {path}") from error
    if not stat.S_ISREG(mode):
        raise StoreLoadError(f"{description} must be a regular file: {path}")
    return path


def _require_contained(root: Path, path: Path, description: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise StoreLoadError(f"cannot resolve {description}: {path}") from error
    if not resolved.is_relative_to(root):
        raise StoreLoadError(f"{description} escapes store root: {path}")
    return resolved


def _load_manifest(path: Path) -> StoreManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StoreLoadError(f"manifest is malformed: {path}") from error
    try:
        return StoreManifest.model_validate(payload)
    except ValueError as error:
        raise StoreLoadError(f"manifest is invalid: {path}: {error}") from error


def load_store(root: Path) -> StoreContext:
    """Load an absolute portable store root without creating any filesystem entries."""
    if not root.is_absolute():
        raise StoreLoadError(f"store root must be absolute: {root}")

    _require_directory(root, "store root")
    resolved_root = _require_contained(root.resolve(strict=True), root, "store root")
    manifest_path = resolved_root / MANIFEST_FILENAME
    items_path = resolved_root / ITEMS_DIRNAME
    index_path = resolved_root / INDEX_FILENAME
    lock_path = resolved_root / LOCK_FILENAME

    _require_regular_file(manifest_path, "manifest")
    _require_contained(resolved_root, manifest_path, "manifest")
    _require_directory(items_path, "items directory")
    _require_contained(resolved_root, items_path, "items directory")
    _require_regular_file(index_path, "index")
    _require_contained(resolved_root, index_path, "index")
    try:
        _require_regular_file(lock_path, "lock")
        _require_contained(resolved_root, lock_path, "lock")
    except StoreLoadError as error:
        if not error.args[0].startswith("lock is missing"):
            raise

    return StoreContext(
        root=resolved_root,
        manifest=_load_manifest(manifest_path),
        manifest_path=manifest_path,
        items_path=items_path,
        index_path=index_path,
        lock_path=lock_path,
    )
