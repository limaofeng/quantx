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
