"""GraphQL resolver bridge for the Engine-owned board assistant."""

import uuid
from dataclasses import fields as dataclass_fields
from datetime import datetime

from quantx_infrastructure.services.engine_command_service import engine_command_service
from quantx_infrastructure.services.limit_up_board_assistant_projection_service import (
  limit_up_board_assistant_projection_service,
)

from quantx_api.gqlapi.types.limit_up_board_assistant_types import (
  LimitUpBoardArmedCandidate,
  LimitUpBoardAssistant,
  LimitUpBoardAssistantMutationResult,
  LimitUpBoardAssistantSettingsInput,
  LimitUpBoardCandidateActionInput,
)


class LimitUpBoardAssistantResolver:
  @staticmethod
  async def _engine_request(
    command_type: str,
    payload: dict,
    aggregate_id: str,
    idempotency_key: str = "",
  ) -> dict:
    receipt = await engine_command_service.request(
      command_type,
      payload,
      aggregate_id=aggregate_id,
      idempotency_key=(
        idempotency_key or f"{command_type.lower()}:{aggregate_id}:{uuid.uuid4()}"
      ),
    )
    if receipt.status == "FAILED":
      raise ValueError(receipt.error or f"Engine command failed: {command_type}")
    if receipt.status != "SUCCEEDED":
      raise ValueError(f"Engine 命令已排队但尚未确认: {receipt.message_id}")
    return dict(receipt.result or {})

  @staticmethod
  def _datetime(value):
    if isinstance(value, datetime) or value is None:
      return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

  @classmethod
  def _assistant_type(cls, data: dict) -> LimitUpBoardAssistant:
    payload = dict(data or {})
    for key in (
      "last_reconciled_at",
      "projection_generated_at",
    ):
      payload[key] = cls._datetime(payload.get(key))
    payload["armed_candidates"] = [
      LimitUpBoardArmedCandidate(
        instrument_code=str(item.get("instrument_code") or ""),
        source=str(item.get("source") or "MANUAL"),
        arm_version=int(item.get("arm_version", 0) or 0),
        armed_at=cls._datetime(item.get("armed_at")),
      )
      for item in list(payload.get("armed_candidates") or [])
    ]
    known = {item.name for item in dataclass_fields(LimitUpBoardAssistant)}
    return LimitUpBoardAssistant(
      **{key: value for key, value in payload.items() if key in known}
    )

  @classmethod
  async def get(cls, account_id: str) -> LimitUpBoardAssistant:
    projection = await limit_up_board_assistant_projection_service.get(account_id)
    if projection is None:
      projection = await cls._engine_request(
        "LIMIT_UP_BOARD_ASSISTANT_GET",
        {"account_id": account_id},
        account_id,
      )
    return cls._assistant_type(projection)

  @classmethod
  async def save(
    cls, input: LimitUpBoardAssistantSettingsInput
  ) -> LimitUpBoardAssistantMutationResult:
    return await cls._mutation(
      "LIMIT_UP_BOARD_ASSISTANT_SAVE",
      {"input": vars(input)},
      input.account_id,
      "BOARD_ASSISTANT_SAVED",
      "打板助手设置已保存",
    )

  @classmethod
  async def reconcile(cls, account_id: str) -> LimitUpBoardAssistantMutationResult:
    return await cls._mutation(
      "LIMIT_UP_BOARD_ASSISTANT_RECONCILE",
      {"account_id": account_id},
      account_id,
      "BOARD_ASSISTANT_RECONCILED",
      "打板助手已重新同步",
    )

  @classmethod
  async def arm(
    cls,
    input: LimitUpBoardCandidateActionInput,
    *,
    actor_id: str,
  ) -> LimitUpBoardAssistantMutationResult:
    payload = {**vars(input), "actor_id": actor_id}
    return await cls._mutation(
      "LIMIT_UP_BOARD_CANDIDATE_ARM",
      payload,
      input.account_id,
      "BOARD_CANDIDATE_ARMED",
      f"{input.instrument_code} 已加入当日布防",
      idempotency_key=input.idempotency_key,
    )

  @classmethod
  async def disarm(
    cls,
    input: LimitUpBoardCandidateActionInput,
    *,
    actor_id: str,
  ) -> LimitUpBoardAssistantMutationResult:
    payload = {**vars(input), "actor_id": actor_id}
    return await cls._mutation(
      "LIMIT_UP_BOARD_CANDIDATE_DISARM",
      payload,
      input.account_id,
      "BOARD_CANDIDATE_DISARMED",
      f"{input.instrument_code} 已取消当日布防",
      idempotency_key=input.idempotency_key,
    )

  @classmethod
  async def _mutation(
    cls,
    command_type: str,
    payload: dict,
    account_id: str,
    success_code: str,
    success_message: str,
    *,
    idempotency_key: str = "",
  ) -> LimitUpBoardAssistantMutationResult:
    try:
      data = await cls._engine_request(
        command_type,
        payload,
        account_id,
        idempotency_key=idempotency_key,
      )
      return LimitUpBoardAssistantMutationResult(
        success=True,
        code=success_code,
        message=success_message,
        assistant=cls._assistant_type(data),
      )
    except ValueError as exc:
      return LimitUpBoardAssistantMutationResult(
        success=False,
        code="VALIDATION_FAILED",
        message=str(exc),
      )
