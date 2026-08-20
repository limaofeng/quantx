from types import SimpleNamespace

import pytest

import quantx_infrastructure.database.backup_registry as registry_module


class FakeSession:
  def __init__(self, rowcount: int):
    self.rowcount = rowcount
    self.committed = False

  async def __aenter__(self):
    return self

  async def __aexit__(self, exc_type, exc, traceback):
    return False

  async def execute(self, _statement):
    return SimpleNamespace(rowcount=self.rowcount)

  async def commit(self):
    self.committed = True


@pytest.mark.asyncio
async def test_record_backup_requires_the_single_account_rollout(
  monkeypatch,
  tmp_path,
):
  manifest = tmp_path / "manifest.json"
  manifest.write_text("{}", encoding="utf-8")
  session = FakeSession(rowcount=1)
  monkeypatch.setattr(registry_module, "AsyncSessionLocal", lambda: session)

  await registry_module.record_backup(str(manifest))

  assert session.committed is True


@pytest.mark.asyncio
@pytest.mark.parametrize("rowcount", [0, 2])
async def test_record_backup_rejects_missing_or_multiple_rollouts(
  monkeypatch,
  tmp_path,
  rowcount,
):
  manifest = tmp_path / "manifest.json"
  manifest.write_text("{}", encoding="utf-8")
  session = FakeSession(rowcount=rowcount)
  monkeypatch.setattr(registry_module, "AsyncSessionLocal", lambda: session)

  with pytest.raises(RuntimeError, match="exactly one account rollout"):
    await registry_module.record_backup(str(manifest))

  assert session.committed is False
