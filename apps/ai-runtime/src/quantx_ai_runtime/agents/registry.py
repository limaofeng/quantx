"""Agent factory kept separate from transport and persistence."""

from __future__ import annotations

import json
from importlib.resources import files

from agents import Agent

from quantx_ai_runtime.tools import RuntimeRunContext, build_tools


def build_agent(
  context: RuntimeRunContext,
  *,
  model: str,
  agent_id: str = "research_assistant",
):
  normalized_agent_id = str(agent_id or "research_assistant")
  prompt_name = (
    "limit_up_research_assistant.md"
    if normalized_agent_id == "limit_up_research_assistant"
    else "research_assistant.md"
  )
  prompt = (
    files("quantx_ai_runtime")
    .joinpath(f"prompts/{prompt_name}")
    .read_text(encoding="utf-8")
  )
  context_refs = [
    {
      "kind": item.kind,
      "objectId": item.object_id,
      "label": item.label,
    }
    for item in context.execution.context_refs
  ]
  if context_refs:
    prompt += (
      "\n\n本次请求由用户显式附加了以下 QuantX 上下文引用。"
      "引用只是对象标识，必须调用获准工具读取事实，不得凭标识猜测内容：\n"
      + json.dumps(context_refs, ensure_ascii=False)
    )
  return Agent(
    name=(
      "QuantX Limit-up Research Assistant"
      if normalized_agent_id == "limit_up_research_assistant"
      else "QuantX Research Assistant"
    ),
    instructions=prompt,
    model=model,
    tools=build_tools(
      context,
      allowed_names=(
        frozenset(
          {
            "get_limit_up_candidate_snapshot",
            "get_limit_up_chain_summary",
            "get_stock_announcement_summary",
          }
        )
        if normalized_agent_id == "limit_up_research_assistant"
        else None
      ),
    ),
  )
