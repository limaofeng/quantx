from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from quantx_ai_runtime.agents.registry import build_agent
from quantx_ai_runtime.runtime import recovery
from quantx_ai_runtime.runtime.limit_up_research import (
  LimitUpResearchOutput,
  _sanitize_citations,
)
from quantx_ai_runtime.runtime.runner import _content_blocks
from quantx_ai_runtime.tools import RuntimeRunContext
from quantx_ai_runtime.tools.registry import _require_persisted_approval
from quantx_application.assistant.contracts import (
  AssistantExecutionContext,
  AssistantToolMetadata,
  AssistantToolRisk,
)


def test_limit_up_agent_has_only_three_read_only_market_tools() -> None:
  context = RuntimeRunContext(
    execution=AssistantExecutionContext(
      user_id="system:limit-up-research",
      permissions=frozenset({"market:read"}),
      authorized_account_ids=(),
      thread_id="market:2026-08-14:000001.SZ",
      run_id="research-job-1",
      request_id="research-job-1",
      external_search_enabled=True,
    )
  )

  agent = build_agent(
    context,
    model="gpt-5-mini",
    agent_id="limit_up_research_assistant",
  )

  assert {tool.name for tool in agent.tools} == {
    "get_limit_up_candidate_snapshot",
    "get_limit_up_chain_summary",
    "get_stock_announcement_summary",
  }


def test_limit_up_research_schema_has_no_executable_recommendation_field() -> None:
  properties = LimitUpResearchOutput.model_json_schema()["properties"]

  assert set(properties) == {
    "candidate_summary",
    "catalysts",
    "announcement_risks",
    "citations",
    "data_gaps",
    "confidence_note",
  }
  assert not ({"buy", "sell", "position", "eligible"} & set(properties))


def test_limit_up_research_drops_citations_not_present_in_persisted_input() -> None:
  output = LimitUpResearchOutput(
    candidate_summary="候选摘要",
    citations=[
      "snapshot-v1",
      "https://exchange.example/notice.pdf",
      "https://untrusted.example/story",
    ],
    confidence_note="仅使用持久化证据",
  )
  announcement = SimpleNamespace(
    source_url="https://exchange.example/notice",
    pdf_url="https://exchange.example/notice.pdf",
  )

  sanitized = _sanitize_citations(
    output,
    announcements=[announcement],
    input_snapshot_version="snapshot-v1",
  )

  assert sanitized.citations == [
    "snapshot-v1",
    "https://exchange.example/notice.pdf",
  ]
  assert sanitized.data_gaps == ["UNVERIFIED_CITATIONS_DROPPED"]


def test_interruption_details_supports_sdk_name_and_json_arguments() -> None:
  interruption = SimpleNamespace(
    name="create_backtest_rerun_task",
    call_id="call-1",
    arguments='{"strategy_run_id":"run-1"}',
  )

  assert recovery.interruption_details(interruption) == (
    "create_backtest_rerun_task",
    "call-1",
    {"strategy_run_id": "run-1"},
  )


@pytest.mark.asyncio
async def test_run_state_restore_uses_original_agent_and_safe_context(
  monkeypatch,
) -> None:
  restored = object()
  from_json = AsyncMock(return_value=restored)
  monkeypatch.setattr(recovery.RunState, "from_json", from_json)
  agent = object()
  payload = {"schema_version": "1"}
  context = {"run_id": "run-1"}

  result = await recovery.deserialize_run_state(
    agent,
    payload,
    context_override=context,
  )

  assert result is restored
  from_json.assert_awaited_once_with(
    agent,
    payload,
    context_override=context,
    strict_context=True,
  )


def test_content_blocks_extract_markdown_and_sdk_url_citations() -> None:
  item = SimpleNamespace(
    raw_item={
      "type": "message",
      "content": [
        {
          "type": "output_text",
          "annotations": [
            {
              "type": "url_citation",
              "title": "交易所公告",
              "url": "https://example.com/disclosure",
            }
          ],
        }
      ],
    }
  )

  blocks = _content_blocks(
    "参考 [公司主页](https://example.com/company)。",
    [item],
  )

  assert blocks[0] == {
    "kind": "TEXT",
    "text": "参考 [公司主页](https://example.com/company)。",
  }
  assert {block.get("url") for block in blocks[1:]} == {
    "https://example.com/disclosure"
  }


def test_non_trading_write_requires_a_persisted_approval() -> None:
  metadata = AssistantToolMetadata(
    name="create_task",
    version="1",
    description="create a task",
    risk_level=AssistantToolRisk.NON_TRADING_WRITE,
  )

  with pytest.raises(PermissionError, match="AI_TOOL_APPROVAL_REQUIRED"):
    _require_persisted_approval(metadata, None)
  with pytest.raises(PermissionError, match="AI_TOOL_APPROVAL_REQUIRED"):
    _require_persisted_approval(
      metadata,
      SimpleNamespace(approval_status="PENDING"),
    )

  _require_persisted_approval(
    metadata,
    SimpleNamespace(approval_status="APPROVED"),
  )
