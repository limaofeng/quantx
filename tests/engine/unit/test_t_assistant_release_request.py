"""Reviewed artifact import/export binds the real source without writing approval."""

import pytest
from quantx_engine.t_assistant_release_request import build_release_request
from quantx_infrastructure.models.t_assistant_execution import (
  TAssistantExecutionEventRecord,
)
from sqlalchemy import func, select

from tests.engine.unit.test_t_assistant_release_approval import (
  NOW,
  release,
  review,
  sessions,
)

_FIXTURES = release, review, sessions


def request_args(review):
  return {
    key: value
    for key, value in review.items()
    if key
    not in {
      "actor_id",
      "review_reference",
      "approval_event_key",
      "expected_head_version",
    }
  }


async def test_request_contains_exact_target_and_no_approval(sessions, review):
  async with sessions() as db:
    before = await db.scalar(
      select(func.count()).select_from(TAssistantExecutionEventRecord)
    )
    result = await build_release_request(db, **request_args(review))
    after = await db.scalar(
      select(func.count()).select_from(TAssistantExecutionEventRecord)
    )
    assert before == after
    assert not db.new and not db.dirty
  assert result["schema"] == "quantx.t-assistant-release-request.v1"
  request = result["request"]
  assert len(request) == 10
  assert request["expectedConfigHash"] == review["expected_config_hash"]
  assert request["expectedHeadVersion"] == 1
  assert request["evaluationId"] == review["evidence_directory"].name
  assert request["windowStart"] == NOW.isoformat()


@pytest.mark.parametrize(
  "change",
  [
    {"expected_config_hash": "0" * 64},
    {"expected_report_hash": "0" * 64},
    {"account_id": "another-account"},
    {"window_end": NOW},
  ],
)
async def test_request_rejects_changed_review_or_source(sessions, review, change):
  async with sessions() as db:
    with pytest.raises(ValueError):
      await build_release_request(db, **{**request_args(review), **change})
    assert not db.new and not db.dirty
