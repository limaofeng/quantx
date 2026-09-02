"""Persist certified datasets and the next-day training queue."""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision = "20260902_0045"
down_revision = "20260901_0044"
branch_labels = None
depends_on = None


_TARGET_TABLES = frozenset(
  {
    "stock_selection_dataset_versions",
    "stock_selection_training_specs",
    "stock_selection_training_runs",
  }
)

# The ORM calls ``create_all`` before Alembic in the development bootstrap
# path.  Keep the adoption contract local to this revision: these are the
# objects that 0045 owns and that must be present before Alembic can safely
# record the revision without replaying CREATE DDL.
_COLUMN_SPECS: dict[
  str, dict[str, tuple[sa.types.TypeEngine, bool, str | None]]
] = {
  "stock_selection_dataset_versions": {
    "dataset_version": (sa.String(length=120), False, None),
    "status": (sa.String(length=16), False, None),
    "source_kind": (sa.String(length=48), False, None),
    "source_reference": (sa.String(length=512), False, None),
    "date_start": (sa.Date(), False, None),
    "date_end": (sa.Date(), False, None),
    "universe_spec": (sa.JSON(), False, None),
    "indicator_version": (sa.String(length=80), False, None),
    "factor_set_version": (sa.String(length=80), False, None),
    "factor_set_hash": (sa.String(length=64), False, None),
    "label_version": (sa.String(length=80), False, None),
    "manifest_sha256": (sa.String(length=64), False, None),
    "sample_count": (sa.Integer(), False, None),
    "stock_count": (sa.Integer(), False, None),
    "trading_day_count": (sa.Integer(), False, None),
    "quality_summary": (sa.JSON(), False, None),
    "created_at": (sa.DateTime(timezone=True), False, "now()"),
  },
  "stock_selection_training_specs": {
    "spec_id": (sa.String(length=36), False, None),
    "dataset_version": (sa.String(length=120), False, None),
    "universe_spec": (sa.JSON(), False, None),
    "run_kind": (sa.String(length=24), False, None),
    "split_spec": (sa.JSON(), False, None),
    "model_spec": (sa.JSON(), False, None),
    "evaluation_spec": (sa.JSON(), False, None),
    "requested_backend": (sa.String(length=16), False, None),
    "resolved_backend": (sa.String(length=32), False, None),
    "random_seed": (sa.Integer(), False, None),
    "worker_batch_size": (sa.Integer(), False, None),
    "note": (sa.String(length=500), False, None),
    "spec_hash": (sa.String(length=64), False, None),
    "environment_requirement_hash": (sa.String(length=64), False, None),
    "coordinate_hash": (sa.String(length=64), False, None),
    "experiment_group_hash": (sa.String(length=64), False, None),
    "frozen_test_access_count": (sa.Integer(), False, None),
    "created_by": (sa.String(length=64), False, None),
    "created_at": (sa.DateTime(timezone=True), False, "now()"),
  },
  "stock_selection_training_runs": {
    "run_id": (sa.String(length=36), False, None),
    "run_key": (sa.String(length=160), True, None),
    "spec_id": (sa.String(length=36), False, None),
    "run_kind": (sa.String(length=24), False, None),
    "parent_run_id": (sa.String(length=36), True, None),
    "status": (sa.String(length=16), False, None),
    "phase": (sa.String(length=24), False, None),
    "completed_units": (sa.Integer(), False, None),
    "total_units": (sa.Integer(), False, None),
    "prefect_flow_run_id": (sa.String(length=128), True, None),
    "requested_at": (sa.DateTime(timezone=True), False, "now()"),
    "started_at": (sa.DateTime(timezone=True), True, None),
    "completed_at": (sa.DateTime(timezone=True), True, None),
    "cancel_requested_at": (sa.DateTime(timezone=True), True, None),
    "cancel_idempotency_key": (sa.String(length=160), True, None),
    "artifact_manifest_sha256": (sa.String(length=64), True, None),
    "environment_evidence": (sa.JSON(), False, None),
    "metrics_summary": (sa.JSON(), False, None),
    "gate_summary": (sa.JSON(), False, None),
    "error_code": (sa.String(length=64), True, None),
    "error_message": (sa.String(length=512), True, None),
    "state_version": (sa.Integer(), False, None),
    "idempotency_key": (sa.String(length=160), False, None),
  },
}

_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
  "stock_selection_dataset_versions": ("dataset_version",),
  "stock_selection_training_specs": ("spec_id",),
  "stock_selection_training_runs": ("run_id",),
}

_UNIQUE_CONSTRAINTS: dict[str, frozenset[tuple[str, ...]]] = {
  "stock_selection_dataset_versions": frozenset(),
  "stock_selection_training_specs": frozenset(),
  "stock_selection_training_runs": frozenset(
    {
      ("run_key",),
      ("idempotency_key",),
    }
  ),
}

_FOREIGN_KEYS: dict[
  str, frozenset[tuple[tuple[str, ...], str, tuple[str, ...], str, str | None]]
] = {
  "stock_selection_dataset_versions": frozenset(),
  "stock_selection_training_specs": frozenset(
    {
      (
        ("dataset_version",),
        "stock_selection_dataset_versions",
        ("dataset_version",),
        "RESTRICT",
        None,
      ),
    }
  ),
  "stock_selection_training_runs": frozenset(
    {
      (
        ("spec_id",),
        "stock_selection_training_specs",
        ("spec_id",),
        "RESTRICT",
        None,
      ),
      (
        ("parent_run_id",),
        "stock_selection_training_runs",
        ("run_id",),
        "RESTRICT",
        None,
      ),
    }
  ),
}

_CHECK_CONSTRAINTS: dict[str, dict[str, str]] = {
  "stock_selection_dataset_versions": {
    "ck_stock_selection_dataset_status": "status IN ('CERTIFIED','RETIRED')",
    "ck_stock_selection_dataset_dates": "date_start <= date_end",
    "ck_stock_selection_dataset_manifest_sha256": "length(manifest_sha256) = 64",
    "ck_stock_selection_dataset_factor_set_hash": "length(factor_set_hash) = 64",
    "ck_stock_selection_dataset_counts": (
      "sample_count >= 0 AND stock_count >= 0 AND trading_day_count >= 0"
    ),
  },
  "stock_selection_training_specs": {
    "ck_stock_selection_training_spec_run_kind": (
      "run_kind IN ('DEVELOPMENT','FINAL_EVALUATION')"
    ),
    "ck_stock_selection_training_spec_requested_backend": (
      "requested_backend IN ('AUTO','CPU','GPU_REQUIRED')"
    ),
    "ck_stock_selection_training_spec_resolved_backend": (
      "resolved_backend IN ('CPU','LIGHTGBM_OPENCL_GPU')"
    ),
    "ck_stock_selection_training_spec_batch_size": (
      "worker_batch_size >= 1 AND worker_batch_size <= 1000"
    ),
    "ck_stock_selection_training_spec_hash_lengths": (
      "length(spec_hash) = 64 AND length(environment_requirement_hash) = 64 "
      "AND length(coordinate_hash) = 64 AND length(experiment_group_hash) = 64"
    ),
    "ck_stock_selection_training_spec_frozen_access_count": (
      "frozen_test_access_count >= 0"
    ),
  },
  "stock_selection_training_runs": {
    "ck_stock_selection_training_run_kind": (
      "run_kind IN ('DEVELOPMENT','FINAL_EVALUATION')"
    ),
    "ck_stock_selection_training_run_status": (
      "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')"
    ),
    "ck_stock_selection_training_run_phase": (
      "phase IN ('PREFLIGHT','DATASET_BUILD','WALK_FORWARD','FINAL_FIT',"
      "'CALIBRATION','FROZEN_TEST','ARTIFACT_PUBLISH')"
    ),
    "ck_stock_selection_training_run_units": (
      "completed_units >= 0 AND total_units >= 0 AND completed_units <= total_units"
    ),
    "ck_stock_selection_training_run_parent_kind": (
      "((run_kind = 'FINAL_EVALUATION' AND parent_run_id IS NOT NULL) OR "
      "(run_kind = 'DEVELOPMENT' AND parent_run_id IS NULL))"
    ),
    "ck_stock_selection_training_run_state_version": "state_version >= 1",
  },
}

_INDEXES: dict[str, dict[str, tuple[tuple[str, ...], bool, str | None]]] = {
  "stock_selection_dataset_versions": {
    "ix_stock_selection_dataset_versions_status": (("status",), False, None),
    "ix_stock_selection_dataset_status_created": (
      ("status", "created_at"),
      False,
      None,
    ),
  },
  "stock_selection_training_specs": {
    "ix_stock_selection_training_specs_dataset_version": (
      ("dataset_version",),
      False,
      None,
    ),
    "ix_stock_selection_training_specs_dataset_kind": (
      ("dataset_version", "run_kind"),
      False,
      None,
    ),
    "ix_stock_selection_training_specs_experiment_group": (
      ("experiment_group_hash",),
      False,
      None,
    ),
  },
  "stock_selection_training_runs": {
    "ix_stock_selection_training_runs_spec_id": (("spec_id",), False, None),
    "ix_stock_selection_training_runs_status": (("status",), False, None),
    "ix_stock_selection_training_runs_status_requested": (
      ("status", "requested_at"),
      False,
      None,
    ),
    "ix_stock_selection_training_runs_parent": (("parent_run_id",), False, None),
    "uq_stock_selection_training_runs_one_running": (
      ("status",),
      True,
      "status = 'RUNNING'",
    ),
  },
}

_TABLE_COMMENTS = {
  "stock_selection_dataset_versions": "次日上涨概率训练认证数据集版本与不可变证据",
  "stock_selection_training_specs": "次日上涨概率训练不可变配置快照",
  "stock_selection_training_runs": "次日上涨概率训练排队、进度、取消与产物状态真源",
}

_NAIVE_UTC_COLUMNS = frozenset(
  {
    ("stock_selection_dataset_versions", "created_at"),
    ("stock_selection_training_specs", "created_at"),
    ("stock_selection_training_runs", "requested_at"),
    ("stock_selection_training_runs", "started_at"),
    ("stock_selection_training_runs", "completed_at"),
    ("stock_selection_training_runs", "cancel_requested_at"),
  }
)


def _type_matches(actual: object, expected: sa.types.TypeEngine) -> bool:
  if actual is None:
    return False
  actual_affinity = getattr(actual, "_type_affinity", type(actual))
  expected_affinity = getattr(expected, "_type_affinity", type(expected))
  if actual_affinity != expected_affinity:
    return False

  # ``JSONB`` shares JSON's affinity but is not the type emitted by this
  # revision.  PostgreSQL reflection returns a dialect JSON class for JSON,
  # so compare the type name rather than requiring the generic class exactly.
  if isinstance(expected, sa.JSON) and type(actual).__name__.upper() == "JSONB":
    return False

  # INTEGER and BIGINT also share an affinity.  The migration deliberately
  # uses INTEGER, and accepting BIGINT would silently widen persisted state.
  if isinstance(expected, sa.Integer) and not isinstance(expected, sa.BigInteger):
    if type(actual).__name__.upper() in {"BIGINT", "BIGINTEGER"}:
      return False

  for attribute in ("length", "precision", "scale", "timezone"):
    expected_value = getattr(expected, attribute, None)
    if expected_value is not None and getattr(actual, attribute, None) != expected_value:
      return False
  return True


def _strip_outer_parentheses(value: str) -> str:
  while value.startswith("(") and value.endswith(")"):
    depth = 0
    closes_at_end = True
    for index, character in enumerate(value):
      if character == "(":
        depth += 1
      elif character == ")":
        depth -= 1
        if depth == 0 and index != len(value) - 1:
          closes_at_end = False
          break
    if not closes_at_end or depth != 0:
      break
    value = value[1:-1].strip()
  return value


def _normalize_sql_expression(value: object) -> str:
  """Normalize PostgreSQL's harmless reflection rewrites, not its meaning."""
  if value is None:
    return ""
  text = _strip_outer_parentheses(str(value).strip())
  parts = re.split(r"('(?:''|[^'])*')", text)
  normalized: list[str] = []
  cast_pattern = re.compile(
    r"::\s*(?:text|character\s+varying|varchar|integer|bigint|"
    r"timestamp(?:\s+with(?:out)?\s+time\s+zone)?)(?:\[\])?",
    flags=re.IGNORECASE,
  )
  for index, part in enumerate(parts):
    if index % 2:
      normalized.append(part)
      continue
    part = re.sub(r'"([A-Za-z_][A-Za-z0-9_]*)"', r"\1", part)
    part = cast_pattern.sub("", part)
    part = re.sub(
      r"\b(and|or|in|is|not|null|any|array)\b",
      lambda match: f" {match.group(1).lower()} ",
      part,
      flags=re.IGNORECASE,
    )
    part = re.sub(r"\s+", " ", part).strip().lower()
    part = re.sub(r"\s*([(),=<>])\s*", r"\1", part)
    normalized.append(part)
  text = "".join(normalized)
  # Punctuation normalization above can join a boolean keyword to a closing
  # or opening parenthesis.  Re-space those keywords outside string literals.
  parts = re.split(r"('(?:''|[^'])*')", text)
  for index in range(0, len(parts), 2):
    parts[index] = re.sub(
      r"\b(and|or|in|is|not|null|any|array)\b",
      lambda match: f" {match.group(1).lower()} ",
      parts[index],
    )
  text = "".join(parts)
  text = re.sub(r"\s+", " ", text).strip()
  text = re.sub(
    r"(?<![a-z0-9_.])\(([a-z_][a-z0-9_.]*)\)",
    r"\1",
    text,
  )
  # PostgreSQL commonly rewrites ``value IN ('a', 'b')`` as
  # ``value = ANY (ARRAY['a'::character varying, 'b'::character varying])``.
  text = re.sub(
    r"([a-z_][a-z0-9_.]*)\s*=\s*any\s*\(\s*\(*\s*array\s*\[([^\]]*)\]\s*\)*\s*\)",
    r"\1 in(\2)",
    text,
  )
  # ``pg_get_constraintdef`` may retain grouping around individual boolean
  # terms.  Remove only redundant groups that contain an operator/boolean
  # expression; value-list parentheses remain intact.
  while True:
    unwrapped = re.sub(
      r"\(([^()]*?(?:>=|<=|<>|!=|=|>|<|\bis\b|\band\b|\bor\b)[^()]*)\)",
      r"\1",
      text,
    )
    if unwrapped == text:
      break
    text = unwrapped
  parts = re.split(r"('(?:''|[^'])*')", text)
  for index in range(0, len(parts), 2):
    parts[index] = re.sub(
      r"\b(and|or|in|is|not|null|any|array)\b",
      lambda match: f" {match.group(1).lower()} ",
      parts[index],
    )
  text = re.sub(r"\s+", " ", "".join(parts)).strip()
  return _strip_outer_parentheses(text)


def _normalize_default(value: object) -> str | None:
  if value is None:
    return None
  normalized = _normalize_sql_expression(value)
  if normalized in {"", "null"}:
    return None
  if normalized in {"current_timestamp", "current_timestamp()", "now"}:
    return "now()"
  return normalized


def _fail(table_name: str, detail: str) -> None:
  raise RuntimeError(
    "Pre-Alembic stock selection training schema mismatch for "
    f"{table_name}: {detail}"
  )


def _is_naive_datetime(value: object) -> bool:
  return isinstance(value, sa.DateTime) and not bool(
    getattr(value, "timezone", False)
  )


def _validate_columns(
  inspector: sa.Inspector,
  table_name: str,
  *,
  allow_known_time_drift: bool = False,
) -> None:
  expected = _COLUMN_SPECS[table_name]
  actual = {
    str(column["name"]): column for column in inspector.get_columns(table_name)
  }
  if set(actual) != set(expected):
    _fail(
      table_name,
      "columns expected="
      + ",".join(sorted(expected))
      + " actual="
      + ",".join(sorted(actual)),
    )
  for name, (expected_type, expected_nullable, expected_default) in expected.items():
    column = actual[name]
    type_matches = _type_matches(column.get("type"), expected_type)
    known_time_drift = (
      (table_name, name) in _NAIVE_UTC_COLUMNS
      and _is_naive_datetime(column.get("type"))
    )
    if not type_matches and not (allow_known_time_drift and known_time_drift):
      _fail(table_name, f"column {name} type mismatch")
    if bool(column.get("nullable", True)) != expected_nullable:
      _fail(table_name, f"column {name} nullable mismatch")
    if column.get("identity") or column.get("computed"):
      _fail(table_name, f"column {name} generated-value metadata mismatch")
    if column.get("comment") not in (None, ""):
      _fail(table_name, f"column {name} comment mismatch")
    if _normalize_default(column.get("default")) != _normalize_default(expected_default):
      _fail(table_name, f"column {name} server default mismatch")


def _validate_primary_key(inspector: sa.Inspector, table_name: str) -> None:
  actual = tuple(
    inspector.get_pk_constraint(table_name).get("constrained_columns") or ()
  )
  if actual != _PRIMARY_KEYS[table_name]:
    _fail(
      table_name,
      f"primary key mismatch expected={_PRIMARY_KEYS[table_name]!r} actual={actual!r}",
    )


def _validate_unique_constraints(inspector: sa.Inspector, table_name: str) -> None:
  actual: set[tuple[str, ...]] = set()
  for constraint in inspector.get_unique_constraints(table_name):
    dialect_options = constraint.get("dialect_options") or {}
    if any(
      value not in (None, False, {}, [], ())
      for value in dialect_options.values()
    ):
      _fail(table_name, "unique constraint options mismatch")
    actual.add(tuple(constraint.get("column_names") or ()))
  if actual != set(_UNIQUE_CONSTRAINTS[table_name]):
    _fail(
      table_name,
      "unique constraints mismatch "
      f"expected={sorted(_UNIQUE_CONSTRAINTS[table_name])!r} "
      f"actual={sorted(actual)!r}",
    )


def _validate_foreign_keys(inspector: sa.Inspector, table_name: str) -> None:
  actual = set()
  for foreign_key in inspector.get_foreign_keys(table_name):
    options = foreign_key.get("options") or {}
    unexpected_options = {
      key: value
      for key, value in options.items()
      if key not in {"ondelete", "onupdate"} and value not in (None, False)
    }
    if unexpected_options:
      _fail(table_name, f"foreign key options mismatch: {unexpected_options!r}")
    actual.add(
      (
        tuple(foreign_key.get("constrained_columns") or ()),
        str(foreign_key.get("referred_table") or ""),
        tuple(foreign_key.get("referred_columns") or ()),
        str(options.get("ondelete") or "").upper() or None,
        str(options.get("onupdate") or "").upper() or None,
      )
    )
  if actual != set(_FOREIGN_KEYS[table_name]):
    _fail(
      table_name,
      "foreign keys mismatch "
      f"expected={sorted(_FOREIGN_KEYS[table_name])!r} actual={sorted(actual)!r}",
    )


def _validate_checks(inspector: sa.Inspector, table_name: str) -> None:
  actual: dict[str, str] = {}
  for constraint in inspector.get_check_constraints(table_name):
    name = constraint.get("name")
    if not name:
      _fail(table_name, "check constraint without a name")
    if str(name) in actual:
      _fail(table_name, f"duplicate check constraint {name}")
    dialect_options = constraint.get("dialect_options") or {}
    if any(value not in (None, False) for value in dialect_options.values()):
      _fail(table_name, f"check constraint {name} options mismatch")
    actual[str(name)] = _normalize_sql_expression(constraint.get("sqltext"))
  expected = {
    name: _normalize_sql_expression(expression)
    for name, expression in _CHECK_CONSTRAINTS[table_name].items()
  }
  if set(actual) != set(expected):
    _fail(
      table_name,
      "check constraints mismatch "
      f"expected={sorted(expected)!r} actual={sorted(actual)!r}",
    )
  for name, expression in expected.items():
    if actual[name] != expression:
      _fail(table_name, f"check constraint {name} expression mismatch")


def _index_predicate(index: dict) -> str | None:
  dialect_options = index.get("dialect_options") or {}
  predicates = [
    dialect_options[key]
    for key in ("postgresql_where", "sqlite_where")
    if dialect_options.get(key) is not None
  ]
  if index.get("where") is not None:
    predicates.append(index["where"])
  if len(predicates) > 1:
    return "__multiple_predicates__"
  return _normalize_sql_expression(predicates[0]) if predicates else None


def _validate_indexes(inspector: sa.Inspector, table_name: str) -> None:
  actual_indexes: dict[str, dict] = {}
  for index in inspector.get_indexes(table_name):
    name = index.get("name")
    if not name:
      _fail(table_name, "index without a name")
    dialect_options = index.get("dialect_options") or {}
    unexpected_options = {
      key: value
      for key, value in dialect_options.items()
      if key not in {"postgresql_where", "sqlite_where"}
      and value not in (None, False, {}, [], ())
    }
    if unexpected_options or index.get("column_sorting"):
      _fail(table_name, f"index {name} options mismatch")
    if index.get("expressions"):
      _fail(table_name, f"index {name} expression mismatch")
    # PostgreSQL may expose the physical indexes backing UNIQUE constraints
    # through get_indexes as well.  Those are validated structurally above and
    # are not 0045's explicitly named index objects.
    columns = tuple(index.get("column_names") or ())
    if bool(index.get("unique")) and not _index_predicate(index):
      duplicates_constraint = index.get("duplicates_constraint")
      if duplicates_constraint:
        continue
    actual_indexes[str(name)] = index

  expected = _INDEXES[table_name]
  if set(actual_indexes) != set(expected):
    _fail(
      table_name,
      "indexes mismatch "
      f"expected={sorted(expected)!r} actual={sorted(actual_indexes)!r}",
    )
  for name, (columns, unique, predicate) in expected.items():
    index = actual_indexes[name]
    if tuple(index.get("column_names") or ()) != columns:
      _fail(table_name, f"index {name} columns mismatch")
    if bool(index.get("unique")) != unique:
      _fail(table_name, f"index {name} uniqueness mismatch")
    expected_predicate = (
      _normalize_sql_expression(predicate) if predicate is not None else None
    )
    if _index_predicate(index) != expected_predicate:
      _fail(table_name, f"index {name} predicate mismatch")


def _validate_comment(inspector: sa.Inspector, table_name: str) -> None:
  try:
    comment = inspector.get_table_comment(table_name)
  except (NotImplementedError, AttributeError) as exc:
    raise RuntimeError(
      f"Pre-Alembic stock selection training schema mismatch for {table_name}: "
      "table comments cannot be reflected"
    ) from exc
  actual = comment.get("text") if comment else None
  if actual != _TABLE_COMMENTS[table_name]:
    _fail(
      table_name,
      f"table comment mismatch expected={_TABLE_COMMENTS[table_name]!r} "
      f"actual={actual!r}",
    )


def _validate_table(
  inspector: sa.Inspector,
  table_name: str,
  *,
  allow_known_time_drift: bool = False,
) -> None:
  _validate_columns(
    inspector,
    table_name,
    allow_known_time_drift=allow_known_time_drift,
  )
  _validate_primary_key(inspector, table_name)
  _validate_unique_constraints(inspector, table_name)
  _validate_foreign_keys(inspector, table_name)
  _validate_checks(inspector, table_name)
  _validate_indexes(inspector, table_name)
  _validate_comment(inspector, table_name)


def _get_inspector() -> sa.Inspector:
  return sa.inspect(op.get_bind())


def _known_naive_utc_columns(inspector: sa.Inspector) -> list[tuple[str, str]]:
  columns_to_convert: list[tuple[str, str]] = []
  for table_name, column_name in sorted(_NAIVE_UTC_COLUMNS):
    columns = {
      str(column["name"]): column
      for column in inspector.get_columns(table_name)
    }
    actual = columns[column_name]
    expected_type = _COLUMN_SPECS[table_name][column_name][0]
    if _type_matches(actual.get("type"), expected_type):
      continue
    if _is_naive_datetime(actual.get("type")):
      columns_to_convert.append((table_name, column_name))
      continue
    _fail(table_name, f"column {column_name} type mismatch")
  return columns_to_convert


def _convert_naive_utc_columns(columns: list[tuple[str, str]]) -> None:
  for table_name, column_name in columns:
    # These identifiers come only from the fixed allow-list above.  The
    # explicit USING clause interprets the existing naive values as UTC while
    # preserving NULLs and the column's existing nullability.
    op.execute(
      sa.text(
        f"ALTER TABLE {table_name} ALTER COLUMN {column_name} "
        "TYPE TIMESTAMP WITH TIME ZONE "
        f"USING {column_name} AT TIME ZONE 'UTC'"
      )
    )


def _adopt_precreated_tables() -> bool:
  inspector = _get_inspector()
  existing_tables = _TARGET_TABLES & set(inspector.get_table_names())
  if not existing_tables:
    return False
  if existing_tables != _TARGET_TABLES:
    _fail(
      "<revision>",
      "schema is partially precreated; "
      f"existing={sorted(existing_tables)!r} "
      f"missing={sorted(_TARGET_TABLES - existing_tables)!r}",
    )
  # Validate every object before any conversion.  Apart from the six known
  # naive-UTC timestamp columns, every reflected object must already match the
  # 0045 contract.  This keeps a partial or unrelated drift fail-closed before
  # any DDL can run.
  for table_name in sorted(_TARGET_TABLES):
    _validate_table(
      inspector,
      table_name,
      allow_known_time_drift=True,
    )
  columns_to_convert = _known_naive_utc_columns(inspector)
  if columns_to_convert:
    _convert_naive_utc_columns(columns_to_convert)
    # Inspect again after ALTER TYPE; Inspector instances cache reflection
    # results, and the post-conversion check is part of the atomic takeover.
    inspector = _get_inspector()
    for table_name in sorted(_TARGET_TABLES):
      _validate_table(inspector, table_name)
  return True


def _created_at() -> sa.Column:
  return sa.Column(
    "created_at",
    sa.DateTime(timezone=True),
    nullable=False,
    server_default=sa.func.now(),
  )


def upgrade() -> None:
  if _adopt_precreated_tables():
    return

  op.create_table(
    "stock_selection_dataset_versions",
    sa.Column("dataset_version", sa.String(120), primary_key=True),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("source_kind", sa.String(48), nullable=False),
    sa.Column("source_reference", sa.String(512), nullable=False),
    sa.Column("date_start", sa.Date(), nullable=False),
    sa.Column("date_end", sa.Date(), nullable=False),
    sa.Column("universe_spec", sa.JSON(), nullable=False),
    sa.Column("indicator_version", sa.String(80), nullable=False),
    sa.Column("factor_set_version", sa.String(80), nullable=False),
    sa.Column("factor_set_hash", sa.String(64), nullable=False),
    sa.Column("label_version", sa.String(80), nullable=False),
    sa.Column("manifest_sha256", sa.String(64), nullable=False),
    sa.Column("sample_count", sa.Integer(), nullable=False),
    sa.Column("stock_count", sa.Integer(), nullable=False),
    sa.Column("trading_day_count", sa.Integer(), nullable=False),
    sa.Column("quality_summary", sa.JSON(), nullable=False),
    _created_at(),
    sa.CheckConstraint(
      "status IN ('CERTIFIED','RETIRED')",
      name="ck_stock_selection_dataset_status",
    ),
    sa.CheckConstraint(
      "date_start <= date_end",
      name="ck_stock_selection_dataset_dates",
    ),
    sa.CheckConstraint(
      "length(manifest_sha256) = 64",
      name="ck_stock_selection_dataset_manifest_sha256",
    ),
    sa.CheckConstraint(
      "length(factor_set_hash) = 64",
      name="ck_stock_selection_dataset_factor_set_hash",
    ),
    sa.CheckConstraint(
      "sample_count >= 0 AND stock_count >= 0 AND trading_day_count >= 0",
      name="ck_stock_selection_dataset_counts",
    ),
    comment="次日上涨概率训练认证数据集版本与不可变证据",
  )
  op.create_index(
    "ix_stock_selection_dataset_versions_status",
    "stock_selection_dataset_versions",
    ["status"],
  )
  op.create_index(
    "ix_stock_selection_dataset_status_created",
    "stock_selection_dataset_versions",
    ["status", "created_at"],
  )

  op.create_table(
    "stock_selection_training_specs",
    sa.Column("spec_id", sa.String(36), primary_key=True),
    sa.Column(
      "dataset_version",
      sa.String(120),
      sa.ForeignKey(
        "stock_selection_dataset_versions.dataset_version", ondelete="RESTRICT"
      ),
      nullable=False,
    ),
    sa.Column("universe_spec", sa.JSON(), nullable=False),
    sa.Column("run_kind", sa.String(24), nullable=False),
    sa.Column("split_spec", sa.JSON(), nullable=False),
    sa.Column("model_spec", sa.JSON(), nullable=False),
    sa.Column("evaluation_spec", sa.JSON(), nullable=False),
    sa.Column("requested_backend", sa.String(16), nullable=False),
    sa.Column("resolved_backend", sa.String(32), nullable=False),
    sa.Column("random_seed", sa.Integer(), nullable=False),
    sa.Column("worker_batch_size", sa.Integer(), nullable=False),
    sa.Column("note", sa.String(500), nullable=False),
    sa.Column("spec_hash", sa.String(64), nullable=False),
    sa.Column("environment_requirement_hash", sa.String(64), nullable=False),
    sa.Column("coordinate_hash", sa.String(64), nullable=False),
    sa.Column("experiment_group_hash", sa.String(64), nullable=False),
    sa.Column("frozen_test_access_count", sa.Integer(), nullable=False),
    sa.Column("created_by", sa.String(64), nullable=False),
    _created_at(),
    sa.CheckConstraint(
      "run_kind IN ('DEVELOPMENT','FINAL_EVALUATION')",
      name="ck_stock_selection_training_spec_run_kind",
    ),
    sa.CheckConstraint(
      "requested_backend IN ('AUTO','CPU','GPU_REQUIRED')",
      name="ck_stock_selection_training_spec_requested_backend",
    ),
    sa.CheckConstraint(
      "resolved_backend IN ('CPU','LIGHTGBM_OPENCL_GPU')",
      name="ck_stock_selection_training_spec_resolved_backend",
    ),
    sa.CheckConstraint(
      "worker_batch_size >= 1 AND worker_batch_size <= 1000",
      name="ck_stock_selection_training_spec_batch_size",
    ),
    sa.CheckConstraint(
      "length(spec_hash) = 64 AND length(environment_requirement_hash) = 64 "
      "AND length(coordinate_hash) = 64 AND length(experiment_group_hash) = 64",
      name="ck_stock_selection_training_spec_hash_lengths",
    ),
    sa.CheckConstraint(
      "frozen_test_access_count >= 0",
      name="ck_stock_selection_training_spec_frozen_access_count",
    ),
    comment="次日上涨概率训练不可变配置快照",
  )
  op.create_index(
    "ix_stock_selection_training_specs_dataset_version",
    "stock_selection_training_specs",
    ["dataset_version"],
  )
  op.create_index(
    "ix_stock_selection_training_specs_dataset_kind",
    "stock_selection_training_specs",
    ["dataset_version", "run_kind"],
  )
  op.create_index(
    "ix_stock_selection_training_specs_experiment_group",
    "stock_selection_training_specs",
    ["experiment_group_hash"],
  )

  op.create_table(
    "stock_selection_training_runs",
    sa.Column("run_id", sa.String(36), primary_key=True),
    sa.Column("run_key", sa.String(160), nullable=True, unique=True),
    sa.Column(
      "spec_id",
      sa.String(36),
      sa.ForeignKey("stock_selection_training_specs.spec_id", ondelete="RESTRICT"),
      nullable=False,
    ),
    sa.Column("run_kind", sa.String(24), nullable=False),
    sa.Column(
      "parent_run_id",
      sa.String(36),
      sa.ForeignKey("stock_selection_training_runs.run_id", ondelete="RESTRICT"),
      nullable=True,
    ),
    sa.Column("status", sa.String(16), nullable=False),
    sa.Column("phase", sa.String(24), nullable=False),
    sa.Column("completed_units", sa.Integer(), nullable=False),
    sa.Column("total_units", sa.Integer(), nullable=False),
    sa.Column("prefect_flow_run_id", sa.String(128), nullable=True),
    sa.Column(
      "requested_at",
      sa.DateTime(timezone=True),
      nullable=False,
      server_default=sa.func.now(),
    ),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("cancel_idempotency_key", sa.String(160), nullable=True),
    sa.Column("artifact_manifest_sha256", sa.String(64), nullable=True),
    sa.Column("environment_evidence", sa.JSON(), nullable=False),
    sa.Column("metrics_summary", sa.JSON(), nullable=False),
    sa.Column("gate_summary", sa.JSON(), nullable=False),
    sa.Column("error_code", sa.String(64), nullable=True),
    sa.Column("error_message", sa.String(512), nullable=True),
    sa.Column("state_version", sa.Integer(), nullable=False),
    sa.Column("idempotency_key", sa.String(160), nullable=False, unique=True),
    sa.CheckConstraint(
      "run_kind IN ('DEVELOPMENT','FINAL_EVALUATION')",
      name="ck_stock_selection_training_run_kind",
    ),
    sa.CheckConstraint(
      "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')",
      name="ck_stock_selection_training_run_status",
    ),
    sa.CheckConstraint(
      "phase IN ('PREFLIGHT','DATASET_BUILD','WALK_FORWARD','FINAL_FIT',"
      "'CALIBRATION','FROZEN_TEST','ARTIFACT_PUBLISH')",
      name="ck_stock_selection_training_run_phase",
    ),
    sa.CheckConstraint(
      "completed_units >= 0 AND total_units >= 0 AND completed_units <= total_units",
      name="ck_stock_selection_training_run_units",
    ),
    sa.CheckConstraint(
      "((run_kind = 'FINAL_EVALUATION' AND parent_run_id IS NOT NULL) OR "
      "(run_kind = 'DEVELOPMENT' AND parent_run_id IS NULL))",
      name="ck_stock_selection_training_run_parent_kind",
    ),
    sa.CheckConstraint(
      "state_version >= 1",
      name="ck_stock_selection_training_run_state_version",
    ),
    comment="次日上涨概率训练排队、进度、取消与产物状态真源",
  )
  op.create_index(
    "ix_stock_selection_training_runs_spec_id",
    "stock_selection_training_runs",
    ["spec_id"],
  )
  op.create_index(
    "ix_stock_selection_training_runs_status",
    "stock_selection_training_runs",
    ["status"],
  )
  op.create_index(
    "ix_stock_selection_training_runs_status_requested",
    "stock_selection_training_runs",
    ["status", "requested_at"],
  )
  op.create_index(
    "ix_stock_selection_training_runs_parent",
    "stock_selection_training_runs",
    ["parent_run_id"],
  )
  op.create_index(
    "uq_stock_selection_training_runs_one_running",
    "stock_selection_training_runs",
    ["status"],
    unique=True,
    postgresql_where=sa.text("status = 'RUNNING'"),
    sqlite_where=sa.text("status = 'RUNNING'"),
  )


def downgrade() -> None:
  raise RuntimeError("QuantX schema downgrades are intentionally disabled")
