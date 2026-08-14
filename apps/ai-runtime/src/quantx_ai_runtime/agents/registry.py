"""Agent factory kept separate from transport and persistence."""

from __future__ import annotations

import json
from importlib.resources import files

from agents import Agent

from quantx_ai_runtime.tools import RuntimeRunContext, build_tools


def build_agent(context: RuntimeRunContext, *, model: str):
  prompt = (
    files("quantx_ai_runtime")
    .joinpath("prompts/research_assistant.md")
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
    name="QuantX Research Assistant",
    instructions=prompt,
    model=model,
    tools=build_tools(context),
  )
