from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from quantx_infrastructure.services.ios_business_notification_projector import (
  BusinessNotificationProjectionSummary,
)

subject = importlib.import_module(
  "quantx_worker.prefector.flows.ios_notification_projection_flow"
)
ROOT = Path(__file__).resolve().parents[2]


class _Session:
  def __init__(self) -> None:
    self.committed = False
    self.rolled_back = False

  async def __aenter__(self):
    return self

  async def __aexit__(self, exc_type, exc, traceback):
    return False

  async def commit(self) -> None:
    self.committed = True

  async def rollback(self) -> None:
    self.rolled_back = True


@pytest.mark.asyncio
async def test_projection_runner_commits_one_bounded_pass(monkeypatch) -> None:
  session = _Session()

  class Projector:
    def __init__(self, db, *, signing_key, source_batch_limit):
      assert db is session
      assert signing_key.startswith(b"configured")
      assert source_batch_limit == 25

    async def project_once(self):
      return BusinessNotificationProjectionSummary(
        discovered=4,
        projected=3,
        already_projected=1,
        queued=2,
      )

  monkeypatch.setattr(subject, "IosBusinessNotificationProjector", Projector)
  result = await subject.run_ios_notification_projection(
    SimpleNamespace(secret_key="configured-auth-secret-key-longer-than-32-bytes"),
    session_factory=lambda: session,
    source_batch_limit=25,
  )

  assert result == {
    "status": "completed",
    "discovered": 4,
    "projected": 3,
    "already_projected": 1,
    "queued": 2,
  }
  assert session.committed is True
  assert session.rolled_back is False


@pytest.mark.asyncio
async def test_projection_runner_rolls_back_failures(monkeypatch) -> None:
  session = _Session()

  class FailingProjector:
    def __init__(self, *_args, **_kwargs):
      pass

    async def project_once(self):
      raise RuntimeError("projection failed")

  monkeypatch.setattr(
    subject,
    "IosBusinessNotificationProjector",
    FailingProjector,
  )
  with pytest.raises(RuntimeError, match="projection failed"):
    await subject.run_ios_notification_projection(
      SimpleNamespace(
        secret_key="configured-auth-secret-key-longer-than-32-bytes"
      ),
      session_factory=lambda: session,
    )
  assert session.committed is False
  assert session.rolled_back is True


@pytest.mark.asyncio
async def test_projection_runner_rejects_unsafe_signing_key() -> None:
  with pytest.raises(RuntimeError, match="configured auth key"):
    await subject.run_ios_notification_projection(
      SimpleNamespace(secret_key="change-this-secret-key"),
      session_factory=_Session,
    )


def test_projection_deployment_is_singleton_every_minute() -> None:
  configuration = yaml.safe_load(
    (ROOT / "apps" / "worker" / "prefect.yaml").read_text(encoding="utf-8")
  )
  deployment = next(
    item
    for item in configuration["deployments"]
    if item["name"] == "ios-business-notification-projection"
  )

  assert deployment["schedules"] == [
    {"cron": "* * * * *", "timezone": "Asia/Shanghai"}
  ]
  assert deployment["concurrency_limit"] == {
    "limit": 1,
    "collision_strategy": "CANCEL_NEW",
    "grace_period_seconds": 60,
  }
