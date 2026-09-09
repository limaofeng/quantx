"""Public GraphQL release contract delegates to device confirmation service."""

from datetime import timedelta

import strawberry
from quantx_api.gqlapi.operation_policy import operation_policy
from quantx_api.gqlapi.schemas import t_assistant_live_schema as live

from tests.api.unit.gqlapi.test_t_assistant_release_confirmation import (
  NOW,
  context,
  release,
  review,
  sessions,
)

_FIXTURES = context, sessions, release, review
SCHEMA = strawberry.Schema(
  query=live.TAssistantLiveQuery, mutation=live.TAssistantLiveMutation
)


async def test_graphql_preview_confirm_and_policy(
  sessions, review, context, monkeypatch
):
  principal, _, _ = context
  monkeypatch.setattr(live, "AsyncSessionLocal", sessions)

  class Clock:
    @staticmethod
    def now(tz):
      return NOW + timedelta(seconds=1)

    @staticmethod
    def fromisoformat(value):
      from datetime import datetime

      return datetime.fromisoformat(value)

  monkeypatch.setattr(live, "datetime", Clock)
  request = {
    key: review[key]
    for key in (
      "account_id",
      "source_execution_id",
      "config_version_id",
      "expected_config_hash",
      "expected_head_version",
      "expected_report_hash",
      "expected_policy_hash",
    )
  }
  request.update(
    evaluation_id=review["evidence_directory"].name,
    window_start=review["window_start"].isoformat(),
    window_end=review["window_end"].isoformat(),
  )

  def camel(key):
    first, *rest = key.split("_")
    return first + "".join(part.title() for part in rest)

  preview = await SCHEMA.execute(
    "mutation($request:TAssistantReleaseRequest!){previewTAssistantLiveRelease(request:$request){success preview{challengeId confirmationToken reportHash windowStart}}}",
    variable_values={"request": {camel(key): value for key, value in request.items()}},
    context_value={"principal": principal},
  )
  assert not preview.errors
  result = preview.data["previewTAssistantLiveRelease"]
  assert (
    result["success"]
    and result["preview"]["reportHash"] == request["expected_report_hash"]
  )
  confirmation = await SCHEMA.execute(
    "mutation($id:String!,$token:String!){confirmTAssistantLiveRelease(challengeId:$id,confirmationToken:$token){success code engineCommandId}}",
    variable_values={
      "id": result["preview"]["challengeId"],
      "token": result["preview"]["confirmationToken"],
    },
    context_value={"principal": principal},
  )
  assert not confirmation.errors
  assert confirmation.data["confirmTAssistantLiveRelease"]["code"] == "RELEASE_QUEUED"
  for name in ("previewTAssistantLiveRelease", "confirmTAssistantLiveRelease"):
    policy = operation_policy("Mutation", name)
    assert policy.required_permissions == ("t-trade:control", "trade:approve")
    assert policy.audiences == ("native",)


async def test_graphql_release_status(sessions, context, monkeypatch):
  principal, issued, _ = context
  monkeypatch.setattr(live, "AsyncSessionLocal", sessions)
  result = await SCHEMA.execute(
    "query($id:String!){tAssistantLiveReleaseStatus(challengeId:$id){challengeId status executionId}}",
    variable_values={"id": issued["challenge_id"]},
    context_value={"principal": principal},
  )
  assert not result.errors
  assert result.data["tAssistantLiveReleaseStatus"]["status"] == "EXPIRED"
  assert result.data["tAssistantLiveReleaseStatus"]["executionId"] is None
  policy = operation_policy("Query", "tAssistantLiveReleaseStatus")
  assert policy.required_permissions == ("t-trade:control", "trade:approve")
  assert policy.audiences == ("native",)


async def test_graphql_release_operations(sessions, context, monkeypatch):
  principal, issued, _ = context
  monkeypatch.setattr(live, "AsyncSessionLocal", sessions)
  result = await SCHEMA.execute(
    'query {tAssistantLiveReleaseOperations(accountId:"account-1",limit:20){challengeId accountId configVersionId createdAt}}',
    context_value={"principal": principal},
  )
  assert not result.errors
  rows = result.data["tAssistantLiveReleaseOperations"]
  assert rows[0]["challengeId"] == issued["challenge_id"]
  assert rows[0]["createdAt"].endswith("+08:00")
  policy = operation_policy("Query", "tAssistantLiveReleaseOperations")
  assert policy.required_permissions == ("t-trade:control", "trade:approve")
  assert policy.audiences == ("native",)
