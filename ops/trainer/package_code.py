"""Build a reproducible Trainer source bundle from one committed Git revision."""

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

SOURCE_PATHS = ("pyproject.toml", "uv.lock", "apps", "packages", "ops", "tests")
REQUIRED_FILES = (
  "pyproject.toml",
  "uv.lock",
  "apps/trainer/pyproject.toml",
  "apps/trainer/src/quantx_trainer/main.py",
)


def _git(repository: Path, *arguments: str, stdout=subprocess.PIPE):
  return subprocess.run(
    ["git", "-C", str(repository), *arguments],
    check=True,
    stdout=stdout,
    stderr=subprocess.PIPE,
  ).stdout


def _portable_path(name: str) -> str:
  path = PurePosixPath(name)
  if path.is_absolute() or str(path) != name or not path.parts:
    raise ValueError("NON_PORTABLE_SOURCE_PATH")
  for part in path.parts:
    stem = part.split(".")[0].upper()
    if (
      part in {".", "..", ".env", ".runtime"}
      or part.endswith((" ", "."))
      or re.search(r'[\\:*?"<>|\x00-\x1f]', part)
      or stem in {"CON", "PRN", "AUX", "NUL"}
      or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem)
    ):
      raise ValueError("NON_PORTABLE_SOURCE_PATH")
  return name


def _write_bundle(archive: Path, destination: Path, commit: str) -> dict:
  entries = []
  seen = set()
  with tarfile.open(archive, "r:") as source:
    members = []
    for member in source:
      name = _portable_path(member.name)
      if name.casefold() in seen:
        raise ValueError("SOURCE_PATH_COLLISION")
      seen.add(name.casefold())
      if member.isdir():
        continue
      if not member.isfile():
        raise ValueError("SOURCE_MUST_BE_REGULAR_FILE")
      members.append(member)
    names = {member.name for member in members}
    if not set(REQUIRED_FILES) <= names:
      raise ValueError("SOURCE_REQUIRED_FILE_MISSING")
    with zipfile.ZipFile(
      destination / "code.zip", "w", compression=zipfile.ZIP_STORED
    ) as bundle:
      for member in sorted(members, key=lambda item: item.name):
        info = zipfile.ZipInfo(member.name, date_time=(1980, 1, 1, 0, 0, 0))
        info.create_system = 3
        mode = 0o755 if member.mode & 0o111 else 0o644
        info.external_attr = (0o100000 | mode) << 16
        digest = hashlib.sha256()
        size = 0
        with source.extractfile(member) as content, bundle.open(info, "w") as target:
          while chunk := content.read(1024 * 1024):
            target.write(chunk)
            digest.update(chunk)
            size += len(chunk)
        if size != member.size:
          raise ValueError("SOURCE_SIZE_MISMATCH")
        entries.append(
          {
            "path": member.name,
            "size": size,
            "sha256": digest.hexdigest(),
            "mode": mode,
          }
        )
  with (destination / "code.zip").open("rb") as content:
    archive_hash = hashlib.file_digest(content, "sha256").hexdigest()
  manifest = {
    "schema_version": 1,
    "kind": "trainer-code",
    "git_commit": commit,
    "source_paths": list(SOURCE_PATHS),
    "lock_sha256": next(
      item["sha256"] for item in entries if item["path"] == "uv.lock"
    ),
    "archive": {
      "path": "code.zip",
      "size": (destination / "code.zip").stat().st_size,
      "sha256": archive_hash,
    },
    "files": entries,
  }
  (destination / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    encoding="utf-8",
  )
  return manifest


def package_code(repository: Path, revision: str, output: Path) -> dict:
  commit = (
    _git(
      repository, "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"
    )
    .decode("ascii")
    .strip()
  )
  if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
    raise ValueError("INVALID_COMMIT")
  output = output.absolute()
  output.parent.mkdir(parents=True, exist_ok=True)
  # Serialize publishers of this destination; never replace a prior release.
  lock = output.with_name(output.name + ".package-lock")
  with lock.open("x"):
    pass
  try:
    if output.exists() or output.is_symlink():
      raise FileExistsError(output)
    with tempfile.TemporaryDirectory(
      prefix=".trainer-package-", dir=output.parent
    ) as tmp:
      staging = Path(tmp) / "bundle"
      staging.mkdir()
      archive = Path(tmp) / "source.tar"
      with archive.open("wb") as target:
        _git(
          repository,
          "archive",
          "--format=tar",
          commit,
          "--",
          *SOURCE_PATHS,
          stdout=target,
        )
      manifest = _write_bundle(archive, staging, commit)
      if output.exists() or output.is_symlink():
        raise FileExistsError(output)
      os.rename(staging, output)
    return manifest
  finally:
    lock.unlink()


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--repository", type=Path, default=Path(__file__).resolve().parents[2]
  )
  parser.add_argument("--revision", required=True)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()
  manifest = package_code(args.repository, args.revision, args.output)
  print(
    json.dumps({"git_commit": manifest["git_commit"], "archive": manifest["archive"]})
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
