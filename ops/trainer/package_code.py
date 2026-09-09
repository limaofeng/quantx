"""Build a reproducible Trainer source bundle from one committed Git revision."""

import argparse
import hashlib
import json
import os
import re
import stat
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


def unpack_code(bundle: Path, manifest_sha256: str, output: Path) -> dict:
  """Verify a separately trusted manifest digest before publishing a new tree."""
  if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
    raise ValueError("INVALID_MANIFEST_DIGEST")
  with (bundle / "manifest.json").open("rb") as source:
    raw = source.read(8 * 1024 * 1024 + 1)
  if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != manifest_sha256:
    raise ValueError("MANIFEST_DIGEST_MISMATCH")
  manifest = json.loads(raw)
  if (
    type(manifest.get("schema_version")) is not int
    or manifest["schema_version"] != 1
    or manifest.get("kind") != "trainer-code"
    or manifest.get("source_paths") != list(SOURCE_PATHS)
    or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", manifest.get("git_commit", ""))
  ):
    raise ValueError("INVALID_CODE_MANIFEST")
  records = {}
  folded = set()
  for item in manifest["files"]:
    name = _portable_path(item["path"])
    if (
      name.casefold() in folded
      or name.split("/")[0] not in SOURCE_PATHS
      or type(item["size"]) is not int
      or item["size"] < 0
      or type(item["mode"]) is not int
      or item["mode"] not in (0o644, 0o755)
      or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
    ):
      raise ValueError("INVALID_FILE_RECORD")
    folded.add(name.casefold())
    records[name] = item
  if not set(REQUIRED_FILES) <= records.keys():
    raise ValueError("SOURCE_REQUIRED_FILE_MISSING")
  # A file cannot also be a directory, including on case-insensitive Windows.
  spellings = {name.casefold(): name for name in records}
  for name in records:
    for parent in PurePosixPath(name).parents:
      key = str(parent).casefold()
      if key in folded or spellings.setdefault(key, str(parent)) != str(parent):
        raise ValueError("SOURCE_PATH_COLLISION")
  if manifest["lock_sha256"] != records["uv.lock"]["sha256"]:
    raise ValueError("LOCK_DIGEST_MISMATCH")
  archive = manifest["archive"]
  if archive["path"] != "code.zip" or type(archive["size"]) is not int:
    raise ValueError("INVALID_ARCHIVE_RECORD")
  output = output.absolute()
  output.parent.mkdir(parents=True, exist_ok=True)
  lock = output.with_name(output.name + ".package-lock")
  with lock.open("x"):
    pass
  try:
    if output.exists() or output.is_symlink():
      raise FileExistsError(output)
    with tempfile.TemporaryDirectory(
      prefix=".trainer-unpack-", dir=output.parent
    ) as tmp:
      staging = Path(tmp) / "code"
      staging.mkdir()
      # Hash and read the same open file; never trust ZIP extraction paths.
      with (bundle / "code.zip").open("rb") as source:
        if (
          os.fstat(source.fileno()).st_size != archive["size"]
          or hashlib.file_digest(source, "sha256").hexdigest() != archive["sha256"]
        ):
          raise ValueError("ARCHIVE_DIGEST_MISMATCH")
        source.seek(0)
        with zipfile.ZipFile(source) as zipped:
          infos = zipped.infolist()
          if (
            len(infos) != len(records)
            or {info.filename for info in infos} != records.keys()
          ):
            raise ValueError("ARCHIVE_INVENTORY_MISMATCH")
          for info in infos:
            record = records[info.filename]
            if (
              info.file_size != record["size"]
              or info.create_system != 3
              or info.external_attr >> 16 != stat.S_IFREG | record["mode"]
              or info.flag_bits & 1
            ):
              raise ValueError("ARCHIVE_FILE_METADATA_MISMATCH")
            target = staging / info.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with zipped.open(info) as content, target.open("xb") as destination:
              while chunk := content.read(1024 * 1024):
                size += len(chunk)
                if size > record["size"]:
                  raise ValueError("ARCHIVE_FILE_SIZE_MISMATCH")
                digest.update(chunk)
                destination.write(chunk)
            if size != record["size"] or digest.hexdigest() != record["sha256"]:
              raise ValueError("ARCHIVE_FILE_DIGEST_MISMATCH")
            target.chmod(record["mode"])
      if output.exists() or output.is_symlink():
        raise FileExistsError(output)
      os.rename(staging, output)
    return manifest
  finally:
    lock.unlink()


def export_dependencies(
  bundle: Path, manifest_sha256: str, output: Path, uv: Path, python: Path
) -> dict:
  for executable in (uv, python):
    if not executable.is_absolute() or not executable.is_file():
      raise ValueError("EXPLICIT_EXECUTABLE_REQUIRED")
  output = output.absolute()
  output.parent.mkdir(parents=True, exist_ok=True)
  lock = output.with_name(output.name + ".package-lock")
  with lock.open("x"):
    pass
  try:
    if output.exists() or output.is_symlink():
      raise FileExistsError(output)
    with tempfile.TemporaryDirectory(
      prefix=".trainer-dependencies-", dir=output.parent
    ) as tmp:
      root = Path(tmp)
      code = root / "code"
      manifest = unpack_code(bundle, manifest_sha256, code)
      staging = root / "dependencies"
      staging.mkdir()
      requirements = staging / "requirements.txt"
      subprocess.run(
        [
          str(uv),
          "export",
          "--project",
          str(code),
          "--locked",
          "--offline",
          "--no-config",
          "--no-python-downloads",
          "--python",
          str(python),
          "--package",
          "quantx-trainer",
          "--no-dev",
          "--no-emit-workspace",
          "--no-header",
          "--no-annotate",
          "--output-file",
          str(requirements),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env={
          key: value for key, value in os.environ.items() if not key.startswith("UV_")
        },
      )
      raw = requirements.read_bytes()
      records = (
        raw.decode("utf-8").replace("\r\n", "\n").replace("\\\n", " ").splitlines()
      )
      records = [line.strip() for line in records if line.strip()]
      if not records or any(
        not re.match(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*==[^\s;]+", line)
        or line.lower().startswith(("quantx-", "xtquant=="))
        or not re.search(r"--hash=sha256:[0-9a-f]{64}(?:\s|$)", line)
        for line in records
      ):
        raise ValueError("INVALID_HASHED_DEPENDENCIES")
      if (
        hashlib.sha256((code / "uv.lock").read_bytes()).hexdigest()
        != manifest["lock_sha256"]
      ):
        raise ValueError("EXPORTED_LOCK_CHANGED")
      evidence = {
        "schema_version": 1,
        "kind": "trainer-external-dependencies",
        "git_commit": manifest["git_commit"],
        "code_manifest_sha256": manifest_sha256,
        "lock_sha256": manifest["lock_sha256"],
        "requirements_sha256": hashlib.sha256(raw).hexdigest(),
        "package_count": len(records),
        "workspace_packages_included": False,
      }
      (staging / "dependencies.json").write_text(
        json.dumps(evidence, sort_keys=True, indent=2) + "\n", encoding="utf-8"
      )
      if output.exists() or output.is_symlink():
        raise FileExistsError(output)
      os.rename(staging, output)
    return evidence
  finally:
    lock.unlink()


def main() -> int:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--repository", type=Path, default=Path(__file__).resolve().parents[2]
  )
  action = parser.add_mutually_exclusive_group(required=True)
  action.add_argument("--revision")
  action.add_argument("--bundle", type=Path)
  parser.add_argument("--manifest-sha256")
  parser.add_argument("--export-dependencies", action="store_true")
  parser.add_argument("--uv", type=Path)
  parser.add_argument("--python", type=Path)
  parser.add_argument("--output", type=Path, required=True)
  args = parser.parse_args()
  if args.export_dependencies:
    if not all((args.bundle, args.manifest_sha256, args.uv, args.python)):
      parser.error(
        "--export-dependencies requires --bundle, --manifest-sha256, --uv and --python"
      )
    print(
      json.dumps(
        export_dependencies(
          args.bundle, args.manifest_sha256, args.output, args.uv, args.python
        )
      )
    )
    return 0
  if args.uv or args.python:
    parser.error("--uv and --python require --export-dependencies")
  if args.bundle:
    if not args.manifest_sha256:
      parser.error("--bundle requires --manifest-sha256 from the packaging host")
    manifest = unpack_code(args.bundle, args.manifest_sha256, args.output)
  else:
    if args.manifest_sha256:
      parser.error("--manifest-sha256 requires --bundle")
    manifest = package_code(args.repository, args.revision, args.output)
  print(
    json.dumps({"git_commit": manifest["git_commit"], "archive": manifest["archive"]})
  )
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
