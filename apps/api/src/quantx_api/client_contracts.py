"""Generate the public, read-only client-development contract bundle."""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI
from graphql import build_schema
from strawberry import Schema

from quantx_api.gqlapi.security import required_permission

CLIENT_OPENAPI_PATHS = frozenset(
  {
    "/auth/session",
    "/auth/session/refresh",
    "/health",
    "/health/live",
    "/health/ready",
    "/health/components",
  }
)
ROOT_OPERATION_TYPES = ("Query", "Mutation", "Subscription")


def _json_bytes(value: Any) -> bytes:
  return (
    json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
  ).encode("utf-8")


def _collect_component_refs(value: Any) -> set[str]:
  refs: set[str] = set()
  if isinstance(value, dict):
    for key, child in value.items():
      if (
        key == "$ref"
        and isinstance(child, str)
        and child.startswith("#/components/")
      ):
        refs.add(child)
      else:
        refs.update(_collect_component_refs(child))
  elif isinstance(value, list):
    for child in value:
      refs.update(_collect_component_refs(child))
  return refs


def _resolve_json_pointer(document: dict[str, Any], pointer: str) -> Any:
  current: Any = document
  for raw_part in pointer.removeprefix("#/").split("/"):
    part = raw_part.replace("~1", "/").replace("~0", "~")
    if not isinstance(current, dict) or part not in current:
      raise KeyError(f"OpenAPI component reference does not exist: {pointer}")
    current = current[part]
  return current


def _insert_json_pointer(
  document: dict[str, Any], pointer: str, value: Any
) -> None:
  parts = [
    part.replace("~1", "/").replace("~0", "~")
    for part in pointer.removeprefix("#/").split("/")
  ]
  current = document
  for part in parts[:-1]:
    current = current.setdefault(part, {})
  current[parts[-1]] = deepcopy(value)


def _pruned_components(
  source: dict[str, Any], paths: dict[str, Any]
) -> dict[str, Any]:
  pruned: dict[str, Any] = {}
  pending = list(_collect_component_refs(paths))
  visited: set[str] = set()
  while pending:
    pointer = pending.pop()
    if pointer in visited:
      continue
    visited.add(pointer)
    component = _resolve_json_pointer(source, pointer)
    _insert_json_pointer(pruned, pointer, component)
    pending.extend(_collect_component_refs(component) - visited)
  return pruned.get("components", {})


def build_client_openapi(app: FastAPI) -> dict[str, Any]:
  """Return a native-client OpenAPI document without internal API surfaces."""
  source = deepcopy(app.openapi())
  paths = {
    path: source["paths"][path]
    for path in sorted(CLIENT_OPENAPI_PATHS)
    if path in source.get("paths", {})
  }
  missing = CLIENT_OPENAPI_PATHS - paths.keys()
  if missing:
    missing_list = ", ".join(sorted(missing))
    raise RuntimeError(f"Client OpenAPI paths are missing: {missing_list}")

  document: dict[str, Any] = {
    "openapi": source["openapi"],
    "info": {
      **source["info"],
      "title": "QuantX Client API",
      "description": (
        "QuantX 原生客户端会话与服务状态接口。业务查询、变更和订阅"
        "使用同源 /graphql。"
      ),
    },
    "paths": paths,
  }
  components = _pruned_components(source, paths)
  if components:
    document["components"] = components
  return document


def build_graphql_permissions(schema_sdl: str) -> dict[str, dict[str, str]]:
  """Map every GraphQL root field to the server authorization permission."""
  graphql_schema = build_schema(schema_sdl)
  result: dict[str, dict[str, str]] = {}
  for operation_name in ROOT_OPERATION_TYPES:
    root_type = graphql_schema.get_type(operation_name)
    fields = getattr(root_type, "fields", None)
    if not isinstance(fields, dict):
      continue
    result[operation_name] = {
      field_name: required_permission(operation_name, field_name)
      for field_name in sorted(fields)
    }
  return result


def build_contract_files(
  app: FastAPI, schema: Schema
) -> dict[str, bytes]:
  schema_sdl = schema.as_str().rstrip() + "\n"
  return {
    "graphql-permissions.json": _json_bytes(
      build_graphql_permissions(schema_sdl)
    ),
    "graphql-schema.graphql": schema_sdl.encode("utf-8"),
    "openapi-client.json": _json_bytes(build_client_openapi(app)),
  }


def write_contract_files(
  output_directory: Path,
  files: dict[str, bytes],
) -> None:
  output_directory.mkdir(parents=True, exist_ok=True)
  expected_names = set(files)
  for existing in output_directory.iterdir():
    if existing.is_file() and existing.name not in expected_names:
      existing.unlink()
  for name, content in files.items():
    (output_directory / name).write_bytes(content)


def _parser() -> argparse.ArgumentParser:
  parser = argparse.ArgumentParser(
    description="Export QuantX native-client API contracts."
  )
  parser.add_argument(
    "--output",
    type=Path,
    required=True,
    help="Directory that receives the generated contract files.",
  )
  return parser


def main(argv: Iterable[str] | None = None) -> int:
  args = _parser().parse_args(list(argv) if argv is not None else None)
  from quantx_api.gqlapi.schema import schema
  from quantx_api.main import app

  write_contract_files(args.output.resolve(), build_contract_files(app, schema))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
