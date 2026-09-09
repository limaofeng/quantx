import hashlib
import importlib.util
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
  "trainer_package_code",
  Path(__file__).resolve().parents[2] / "ops/trainer/package_code.py",
)
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


def archive(path, extra=(), *, reverse=False):
  records = [
    (name, b"committed content", tarfile.REGTYPE) for name in package.REQUIRED_FILES
  ]
  records.extend(extra)
  with tarfile.open(path, "w") as target:
    for name, content, kind in reversed(records) if reverse else records:
      info = tarfile.TarInfo(name)
      info.type = kind
      info.size = len(content) if kind == tarfile.REGTYPE else 0
      info.mtime = 1780000000 if reverse else 1000000000
      info.mode = 0o644
      info.linkname = "uv.lock" if kind != tarfile.REGTYPE else ""
      target.addfile(info, io.BytesIO(content) if info.size else None)


def test_reproducible_bundle_and_complete_inventory(tmp_path):
  manifests = []
  for index in range(2):
    source = tmp_path / f"source-{index}.tar"
    archive(
      source, [("apps/研究.txt", b"research", tarfile.REGTYPE)], reverse=bool(index)
    )
    output = tmp_path / str(index)
    output.mkdir()
    manifests.append(package._write_bundle(source, output, "a" * 40))
    with zipfile.ZipFile(output / "code.zip") as bundle:
      assert set(bundle.namelist()) == {item["path"] for item in manifests[-1]["files"]}
      for item in manifests[-1]["files"]:
        content = bundle.read(item["path"])
        assert len(content) == item["size"]
        assert hashlib.sha256(content).hexdigest() == item["sha256"]
    assert json.loads((output / "manifest.json").read_text()) == manifests[-1]
  assert manifests[0] == manifests[1]
  assert (tmp_path / "0/code.zip").read_bytes() == (
    tmp_path / "1/code.zip"
  ).read_bytes()


@pytest.mark.parametrize(
  "name,kind",
  [
    ("../escape", tarfile.REGTYPE),
    ("apps/link", tarfile.SYMTYPE),
    ("apps/link", tarfile.LNKTYPE),
    ("apps/UV.lock:stream", tarfile.REGTYPE),
    ("apps/CON.txt", tarfile.REGTYPE),
    ("apps/.env", tarfile.REGTYPE),
    ("UV.LOCK", tarfile.REGTYPE),
  ],
)
def test_rejects_unsafe_or_ambiguous_archive(tmp_path, name, kind):
  source = tmp_path / "source.tar"
  archive(source, [(name, b"bad", kind)])
  with pytest.raises(ValueError):
    package._write_bundle(source, tmp_path, "a" * 40)


def test_resolves_revision_once_and_never_reads_working_tree(tmp_path, monkeypatch):
  source = tmp_path / "source.tar"
  archive(source)
  (tmp_path / "uv.lock").write_text("UNCOMMITTED PRIVATE CONTENT")
  calls = []

  def git(repository, *args, stdout=None):
    calls.append(args)
    if args[0] == "rev-parse":
      return ("a" * 40 + "\n").encode()
    assert args == ("archive", "--format=tar", "a" * 40, "--", *package.SOURCE_PATHS)
    stdout.write(source.read_bytes())

  monkeypatch.setattr(package, "_git", git)
  output = tmp_path / "release"
  manifest = package.package_code(tmp_path, "HEAD", output)
  assert len(calls) == 2
  assert manifest["git_commit"] == "a" * 40
  with zipfile.ZipFile(output / "code.zip") as bundle:
    assert bundle.read("uv.lock") == b"committed content"
  before = (output / "manifest.json").read_bytes()
  with pytest.raises(FileExistsError):
    package.package_code(tmp_path, "HEAD", output)
  assert (output / "manifest.json").read_bytes() == before
  assert not list(tmp_path.glob(".trainer-package-*"))
  assert not list(tmp_path.glob("*.package-lock"))


def test_archive_failure_does_not_publish_partial_directory(tmp_path, monkeypatch):
  def git(repository, *args, stdout=None):
    if args[0] == "rev-parse":
      return b"a" * 40
    stdout.write(b"partial archive")
    raise RuntimeError("archive failed")

  monkeypatch.setattr(package, "_git", git)
  with pytest.raises(RuntimeError):
    package.package_code(tmp_path, "HEAD", tmp_path / "release")
  assert not (tmp_path / "release").exists()
  assert not list(tmp_path.iterdir())


def make_bundle(tmp_path):
  source = tmp_path / "source.tar"
  archive(source)
  bundle = tmp_path / "bundle"
  bundle.mkdir()
  package._write_bundle(source, bundle, "a" * 40)
  return bundle


def manifest_digest(bundle):
  return hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()


def test_unpack_verified_tree_and_refuse_replacement(tmp_path):
  bundle = make_bundle(tmp_path)
  output = tmp_path / "code"
  manifest = package.unpack_code(bundle, manifest_digest(bundle), output)
  files = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
  assert files == {f["path"] for f in manifest["files"]}
  for item in manifest["files"]:
    assert (
      hashlib.sha256((output / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    )
  (output / "uv.lock").write_text("existing installation")
  with pytest.raises(FileExistsError):
    package.unpack_code(bundle, manifest_digest(bundle), output)
  assert (output / "uv.lock").read_text() == "existing installation"


@pytest.mark.parametrize(
  "name", ["apps/TRAINER/extra.py", "apps/trainer/pyproject.toml/extra.py"]
)
def test_unpack_rejects_directory_collisions(tmp_path, name):
  bundle = make_bundle(tmp_path)
  manifest = json.loads((bundle / "manifest.json").read_text())
  manifest["files"].append({**manifest["files"][0], "path": name})
  (bundle / "manifest.json").write_text(json.dumps(manifest))
  with pytest.raises(ValueError, match="SOURCE_PATH_COLLISION"):
    package.unpack_code(bundle, manifest_digest(bundle), tmp_path / "code")
  assert not (tmp_path / "code").exists()


@pytest.mark.parametrize(
  "fault",
  [
    "manifest",
    "archive",
    "file_hash",
    "file_size",
    "path",
    "lock",
    "duplicate",
    "link",
    "extra",
  ],
)
def test_unpack_rejects_corruption_without_partial_output(tmp_path, fault):
  bundle = make_bundle(tmp_path)
  manifest = json.loads((bundle / "manifest.json").read_text())
  expected = manifest_digest(bundle)
  if fault == "manifest":
    manifest["git_commit"] = "b" * 40
  elif fault == "archive":
    with (bundle / "code.zip").open("ab") as target:
      target.write(b"altered")
  elif fault == "file_hash":
    manifest["files"][0]["sha256"] = "b" * 64
  elif fault == "file_size":
    manifest["files"][0]["size"] += 1
  elif fault == "path":
    manifest["files"][0]["path"] = "../escape"
  elif fault == "lock":
    manifest["lock_sha256"] = "b" * 64
  else:
    # Recompute outer hashes so metadata and exact inventory checks are exercised.
    with zipfile.ZipFile(bundle / "code.zip") as source:
      content = [(info, source.read(info)) for info in source.infolist()]
    with zipfile.ZipFile(bundle / "code.zip", "w") as target:
      for index, (info, data) in enumerate(content):
        if fault == "link" and index == 0:
          info.external_attr = 0o120777 << 16
        target.writestr(info, data)
      if fault == "extra":
        target.writestr("extra.py", b"unlisted")
      if fault == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
          target.writestr(*content[0])
    manifest["archive"]["sha256"] = hashlib.sha256(
      (bundle / "code.zip").read_bytes()
    ).hexdigest()
    manifest["archive"]["size"] = (bundle / "code.zip").stat().st_size
  (bundle / "manifest.json").write_text(json.dumps(manifest))
  if fault != "manifest":
    expected = manifest_digest(bundle)
  with pytest.raises(ValueError):
    package.unpack_code(bundle, expected, tmp_path / "code")
  assert not (tmp_path / "code").exists()
  assert not (tmp_path / "escape").exists()
  assert not list(tmp_path.glob(".trainer-unpack-*"))
  assert not list(tmp_path.glob("*.package-lock"))
