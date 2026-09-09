"""Versioned, host-independent inventory for frozen datasets and results."""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_COMPONENT = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_RESERVED = {
  "CON",
  "PRN",
  "AUX",
  "NUL",
  *(f"COM{i}" for i in range(1, 10)),
  *(f"LPT{i}" for i in range(1, 10)),
}


class BundleFile(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
  path: str = Field(min_length=1, max_length=512)
  size: int = Field(ge=0)
  sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

  @field_validator("path")
  @classmethod
  def portable_relative_path(cls, value: str) -> str:
    for component in value.split("/"):
      if (
        not _COMPONENT.fullmatch(component)
        or component.endswith(".")
        or component.split(".")[0].upper() in _RESERVED
      ):
        raise ValueError("bundle entries require portable relative file names")
    return value


class TrainingBundle(BaseModel):
  model_config = ConfigDict(extra="forbid", frozen=True)
  schema_version: Literal[1]
  kind: Literal["DATASET", "RESULT", "RELEASE", "CERTIFICATION_INPUT"]
  source_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
  files: tuple[BundleFile, ...] = Field(min_length=1, max_length=10000)

  @field_validator("schema_version", mode="before")
  @classmethod
  def exact_version(cls, value):
    if type(value) is not int or value != 1:
      raise ValueError("unsupported training bundle version")
    return value

  @model_validator(mode="after")
  def unique_portable_tree(self):
    prefixes: dict[str, str] = {}
    files = set()
    directories = set()
    for entry in self.files:
      parts = entry.path.split("/")
      for count in range(1, len(parts) + 1):
        prefix = "/".join(parts[:count])
        folded = prefix.casefold()
        if folded in prefixes and prefixes[folded] != prefix:
          raise ValueError("bundle paths collide on a case-insensitive host")
        if count < len(parts) and folded in files:
          raise ValueError("bundle file is also used as a directory")
        if count < len(parts):
          directories.add(folded)
        prefixes[folded] = prefix
      folded = entry.path.casefold()
      if folded in files or folded in directories:
        raise ValueError("bundle contains duplicate or conflicting file paths")
      files.add(folded)
    return self

  def canonical_bytes(self) -> bytes:
    payload = self.model_dump(mode="json")
    payload["files"] = sorted(payload["files"], key=lambda entry: entry["path"])
    return json.dumps(
      payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()

  @property
  def bundle_id(self) -> str:
    return hashlib.sha256(self.canonical_bytes()).hexdigest()

  @property
  def total_bytes(self) -> int:
    return sum(entry.size for entry in self.files)
