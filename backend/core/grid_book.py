"""GridBook helpers for Pullback Grid inventory-ledger snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


GRID_BOOK_CUSTOM_STATE_KEY = "grid_book_snapshot"
GRID_BOOK_MODEL_VERSION = 2
INVENTORY_MODEL = "INVENTORY_LEDGER_GRID"
RELEASE_RULE = "NEAREST_LOWER"
SELL_EMPTY_BEHAVIOR = "WAIT_FOR_INVENTORY"

GRID_STATUSES = {
  "DISABLED",
  "PLANNED",
  "MONITORING",
  "WAIT_REARM",
  "PENDING",
  "PARTIAL_FILLED",
  "FILLED",
  "REJECTED",
  "CANCELLED",
}
LOCKED_GRID_STATUSES = {"PENDING", "PARTIAL_FILLED", "FILLED"}
LOCKED_GRID_BOOK_STATUSES = LOCKED_GRID_STATUSES
LOT_STATUSES = {"OPEN", "RESERVED", "CLOSED", "CANCELLED"}


def now_iso() -> str:
  return datetime.now().isoformat()


def _value(data: Dict[str, Any], *keys: str, default: Any = None) -> Any:
  for key in keys:
    if key in data:
      return data.get(key)
  return default


def _float(value: Any, default: float = 0.0) -> float:
  try:
    return float(value if value is not None else default)
  except (TypeError, ValueError):
    return default


def _int(value: Any, default: int = 0) -> int:
  try:
    return int(value if value is not None else default)
  except (TypeError, ValueError):
    return default


def normalize_status(status: Any, default: str = "PLANNED", enabled: bool = True) -> str:
  if not enabled:
    return "DISABLED"
  value = str(status or default).upper()
  return value if value in GRID_STATUSES else default


def normalize_lot_status(status: Any, remaining_shares: int = 0) -> str:
  value = str(status or "").upper()
  if value in LOT_STATUSES:
    return value
  return "OPEN" if remaining_shares > 0 else "CLOSED"


def normalize_level(raw: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
  data = dict(raw or {})
  side = str(_value(data, "side", default="BUY") or "BUY").upper()
  planned_shares = _int(
    _value(data, "planned_shares", "plannedShares", "shares", "volume", default=0)
  )
  price = _float(_value(data, "price", "trigger_price", "triggerPrice", default=0.0))
  amount = _float(_value(data, "amount", default=price * planned_shares))
  grid_id = str(
    _value(data, "grid_id", "gridId", "id", default=f"{side.lower()}-{index + 1}")
  )
  role = str(
    _value(
      data,
      "role",
      default="BUY_SLOT" if side == "BUY" else "SELL_WATERLINE",
    )
    or ("BUY_SLOT" if side == "BUY" else "SELL_WATERLINE")
  ).upper()
  enabled = bool(_value(data, "enabled", default=True))
  status = normalize_status(_value(data, "status", default="PLANNED"))
  if not enabled:
    status = "DISABLED"

  return {
    "grid_id": grid_id,
    "level_index": _int(_value(data, "level_index", "levelIndex", default=index + 1)),
    "side": side,
    "role": role,
    "price": price,
    "planned_shares": planned_shares,
    "amount": amount,
    "pct_from_base": _float(_value(data, "pct_from_base", "pctFromBase", default=0.0)),
    "expected_profit": _float(_value(data, "expected_profit", "expectedProfit", default=0.0)),
    "enabled": enabled,
    "status": status,
    "monitoring": bool(_value(data, "monitoring", "is_monitoring", default=False)),
    "pending_shares": _int(
      _value(data, "pending_shares", "pendingShares", "pending_volume", default=0)
    ),
    "filled_shares": _int(
      _value(data, "filled_shares", "filledShares", "filled_volume", default=0)
    ),
    "available_inventory_shares": _int(
      _value(data, "available_inventory_shares", "availableInventoryShares", default=0)
    ),
    "reserved_inventory_shares": _int(
      _value(data, "reserved_inventory_shares", "reservedInventoryShares", default=0)
    ),
    "cycle_count": _int(_value(data, "cycle_count", "cycleCount", default=0)),
    "waiting_reason": str(_value(data, "waiting_reason", "waitingReason", default="") or ""),
    "order_id": _value(data, "order_id", "orderId", default=None),
    "entry_price": _value(data, "entry_price", "entryPrice", default=None),
    "entry_time": _value(data, "entry_time", "entryTime", default=None),
    "last_intent_id": _value(data, "last_intent_id", "lastIntentId", default=None),
    "last_trace_id": _value(data, "last_trace_id", "lastTraceId", default=None),
    "reason": str(_value(data, "reason", default="") or ""),
    "updated_at": _value(data, "updated_at", "updatedAt", default=now_iso()),
  }


def normalize_inventory_lot(raw: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
  data = dict(raw or {})
  remaining = _int(_value(data, "remaining_shares", "remainingShares", default=0))
  reserved = _int(_value(data, "reserved_shares", "reservedShares", default=0))
  lot_id = str(_value(data, "lot_id", "lotId", "id", default=f"lot-{index + 1}") or f"lot-{index + 1}")
  return {
    "lot_id": lot_id,
    "source_level_id": _value(data, "source_level_id", "sourceLevelId", default=None),
    "source_level_index": _value(data, "source_level_index", "sourceLevelIndex", default=None),
    "source": str(_value(data, "source", default="BUY_FILL") or "BUY_FILL").upper(),
    "bucket": str(_value(data, "bucket", default="swing") or "swing").lower(),
    "entry_price": _float(_value(data, "entry_price", "entryPrice", default=0.0)),
    "original_shares": _int(_value(data, "original_shares", "originalShares", default=remaining)),
    "remaining_shares": max(0, remaining),
    "reserved_shares": max(0, min(reserved, remaining)),
    "reserved_for_level_id": _value(data, "reserved_for_level_id", "reservedForLevelId", default=None),
    "reserved_order_id": _value(data, "reserved_order_id", "reservedOrderId", default=None),
    "target_sell_level_id": _value(data, "target_sell_level_id", "targetSellLevelId", default=None),
    "target_sell_level_index": _value(data, "target_sell_level_index", "targetSellLevelIndex", default=None),
    "status": normalize_lot_status(_value(data, "status", default=None), remaining),
    "created_at": _value(data, "created_at", "createdAt", default=now_iso()),
    "updated_at": _value(data, "updated_at", "updatedAt", default=now_iso()),
  }


def normalize_release_event(raw: Dict[str, Any], index: int = 0) -> Dict[str, Any]:
  data = dict(raw or {})
  return {
    "event_id": str(_value(data, "event_id", "eventId", "id", default=f"release-{index + 1}")),
    "sell_level_id": _value(data, "sell_level_id", "sellLevelId", default=None),
    "sell_level_index": _value(data, "sell_level_index", "sellLevelIndex", default=None),
    "released_level_id": _value(data, "released_level_id", "releasedLevelId", default=None),
    "released_level_index": _value(data, "released_level_index", "releasedLevelIndex", default=None),
    "lot_ids": list(_value(data, "lot_ids", "lotIds", default=[]) or []),
    "order_id": _value(data, "order_id", "orderId", default=None),
    "intent_id": _value(data, "intent_id", "intentId", default=None),
    "trade_id": _value(data, "trade_id", "tradeId", default=None),
    "price": _float(_value(data, "price", default=0.0)),
    "shares": _int(_value(data, "shares", default=0)),
    "created_at": _value(data, "created_at", "createdAt", default=now_iso()),
  }


def summarize_levels(
  levels: Iterable[Dict[str, Any]],
  inventory_lots: Optional[Iterable[Dict[str, Any]]] = None,
  release_events: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
  rows = list(levels or [])
  lots = list(inventory_lots or [])
  events = list(release_events or [])
  open_lot_shares = sum(
    max(0, _int(lot.get("remaining_shares")) - _int(lot.get("reserved_shares")))
    for lot in lots
    if str(lot.get("status", "")).upper() in {"OPEN", "RESERVED"}
  )
  reserved_lot_shares = sum(
    _int(lot.get("reserved_shares"))
    for lot in lots
    if str(lot.get("status", "")).upper() in {"OPEN", "RESERVED"}
  )
  return {
    "total_levels": len(rows),
    "enabled_levels": sum(1 for level in rows if level.get("enabled", True)),
    "pending_levels": sum(1 for level in rows if level.get("status") == "PENDING"),
    "filled_levels": sum(1 for level in rows if level.get("status") == "FILLED"),
    "disabled_levels": sum(1 for level in rows if not level.get("enabled", True)),
    "planned_amount": sum(
      _float(level.get("amount"))
      for level in rows
      if level.get("enabled", True) and level.get("status") != "DISABLED"
    ),
    "buy_slot_count": sum(1 for level in rows if level.get("role") == "BUY_SLOT"),
    "sell_waterline_count": sum(1 for level in rows if level.get("role") == "SELL_WATERLINE"),
    "open_lot_shares": open_lot_shares,
    "reserved_lot_shares": reserved_lot_shares,
    "waiting_inventory_levels": sum(
      1 for level in rows if level.get("waiting_reason") == "waiting_swing_inventory"
    ),
    "completed_cycles": sum(_int(level.get("cycle_count")) for level in rows),
    "release_event_count": len(events),
  }


def _decorate_level_inventory(
  levels: List[Dict[str, Any]], inventory_lots: List[Dict[str, Any]]
) -> None:
  for level in levels:
    if level.get("side") == "SELL":
      available = 0
      reserved = 0
      price = _float(level.get("price"))
      for lot in inventory_lots:
        if str(lot.get("bucket", "")).lower() != "swing":
          continue
        if str(lot.get("status", "")).upper() not in {"OPEN", "RESERVED"}:
          continue
        if not _lot_targets_sell_level(lot, level):
          continue
        if _float(lot.get("entry_price")) >= price:
          continue
        available += max(0, _int(lot.get("remaining_shares")) - _int(lot.get("reserved_shares")))
        if lot.get("reserved_for_level_id") == level.get("grid_id"):
          reserved += _int(lot.get("reserved_shares"))
      level["available_inventory_shares"] = available
      level["reserved_inventory_shares"] = reserved
      if (
        level.get("enabled", True)
        and level.get("status") in {"PLANNED", "MONITORING"}
        and available < _int(level.get("planned_shares"))
      ):
        level["waiting_reason"] = "waiting_swing_inventory"
      elif level.get("waiting_reason") == "waiting_swing_inventory":
        level["waiting_reason"] = ""
    else:
      source_id = level.get("grid_id")
      level["available_inventory_shares"] = sum(
        max(0, _int(lot.get("remaining_shares")) - _int(lot.get("reserved_shares")))
        for lot in inventory_lots
        if lot.get("source_level_id") == source_id
        and str(lot.get("status", "")).upper() in {"OPEN", "RESERVED"}
      )
      level["reserved_inventory_shares"] = sum(
        _int(lot.get("reserved_shares"))
        for lot in inventory_lots
        if lot.get("source_level_id") == source_id
      )


def _lot_targets_sell_level(lot: Dict[str, Any], level: Dict[str, Any]) -> bool:
  target_id = lot.get("target_sell_level_id")
  target_index = lot.get("target_sell_level_index")
  if target_id and str(target_id) != str(level.get("grid_id")):
    return False
  if target_index is not None and target_index != "":
    try:
      if int(target_index) != _int(level.get("level_index")):
        return False
    except (TypeError, ValueError):
      return False
  return True


def normalize_grid_book(
  raw: Optional[Dict[str, Any]],
  *,
  run_id: str = "",
  instrument_code: str = "",
  base_price: float = 0.0,
  parameter_version: str = "",
  editable: bool = False,
  needs_backtest: bool = False,
  parameters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
  data = dict(raw or {})
  raw_levels = data.get("levels") or []
  raw_lots = data.get("inventory_lots", data.get("inventoryLots", [])) or []
  raw_events = data.get("release_events", data.get("releaseEvents", [])) or []
  levels = [normalize_level(level, index) for index, level in enumerate(raw_levels)]
  inventory_lots = [
    normalize_inventory_lot(lot, index) for index, lot in enumerate(raw_lots)
  ]
  release_events = [
    normalize_release_event(event, index) for index, event in enumerate(raw_events)
  ]
  _decorate_level_inventory(levels, inventory_lots)
  summary = summarize_levels(levels, inventory_lots, release_events)
  return {
    "run_id": str(data.get("run_id", data.get("runId", run_id)) or run_id),
    "instrument_code": str(
      data.get("instrument_code", data.get("instrumentCode", instrument_code))
      or instrument_code
    ),
    "base_price": _float(data.get("base_price", data.get("basePrice", base_price))),
    "parameter_version": str(
      data.get("parameter_version", data.get("parameterVersion", parameter_version))
      or parameter_version
    ),
    "version": _int(data.get("version", 1), 1),
    "model_version": _int(data.get("model_version", data.get("modelVersion", GRID_BOOK_MODEL_VERSION))),
    "inventory_model": str(
      data.get("inventory_model", data.get("inventoryModel", INVENTORY_MODEL))
      or INVENTORY_MODEL
    ),
    "release_rule": str(data.get("release_rule", data.get("releaseRule", RELEASE_RULE)) or RELEASE_RULE),
    "sell_empty_behavior": str(
      data.get("sell_empty_behavior", data.get("sellEmptyBehavior", SELL_EMPTY_BEHAVIOR))
      or SELL_EMPTY_BEHAVIOR
    ),
    "editable": bool(data.get("editable", editable)),
    "needs_backtest": bool(data.get("needs_backtest", data.get("needsBacktest", needs_backtest))),
    "summary": summary,
    "levels": levels,
    "inventory_lots": inventory_lots,
    "release_events": release_events,
    "updated_at": data.get("updated_at", data.get("updatedAt", now_iso())),
  }


def build_grid_book_from_parameters(
  parameters: Dict[str, Any],
  *,
  run_id: str,
  instrument_code: str = "",
  editable: bool = False,
  needs_backtest: bool = False,
) -> Dict[str, Any]:
  params = dict(parameters or {})
  raw_levels = params.get("grid_levels") or params.get("gridLevels") or []
  levels = []
  for index, raw in enumerate(raw_levels):
    level = normalize_level(raw, index)
    levels.append(level)

  instrument_code = str(instrument_code or params.get("instrument_code", "") or "")
  if not instrument_code:
    stock_codes = params.get("stockCodes", params.get("stock_codes", ""))
    if isinstance(stock_codes, list):
      instrument_code = str(stock_codes[0] if stock_codes else "")
    else:
      instrument_code = str(stock_codes or "").split(",")[0].strip()

  lots = []
  swing_shares = _int(params.get("swing_shares", params.get("initial_swing_shares", 0)))
  if swing_shares > 0:
    entry_price = _float(params.get("avg_cost", params.get("base_price", 0.0)))
    lots.extend(
      build_initial_swing_lots(
        swing_shares=swing_shares,
        entry_price=entry_price,
        owner=instrument_code or run_id,
        sell_levels=levels,
      )
    )

  return normalize_grid_book(
    {
      "run_id": run_id,
      "instrument_code": instrument_code,
      "base_price": _float(params.get("base_price", 0.0)),
      "parameter_version": str(params.get("_parameter_version", "")),
      "model_version": GRID_BOOK_MODEL_VERSION,
      "inventory_model": INVENTORY_MODEL,
      "release_rule": RELEASE_RULE,
      "sell_empty_behavior": SELL_EMPTY_BEHAVIOR,
      "levels": levels,
      "inventory_lots": lots,
      "release_events": [],
    },
    run_id=run_id,
    editable=editable,
    needs_backtest=needs_backtest,
  )


def build_initial_swing_lots(
  *,
  swing_shares: int,
  entry_price: float,
  owner: str,
  sell_levels: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
  shares_remaining = max(0, _int(swing_shares))
  if shares_remaining <= 0:
    return []

  now = now_iso()
  eligible_sell_levels = sorted(
    [
      level
      for level in sell_levels
      if level.get("enabled", True)
      and str(level.get("side", "")).upper() == "SELL"
      and _int(level.get("planned_shares")) > 0
    ],
    key=lambda level: (_float(level.get("price")), _int(level.get("level_index"))),
  )
  if not eligible_sell_levels:
    return [
      {
        "lot_id": f"initial-swing-{owner}",
        "source_level_id": None,
        "source_level_index": None,
        "source": "INITIAL_SWING",
        "bucket": "swing",
        "entry_price": entry_price,
        "original_shares": shares_remaining,
        "remaining_shares": shares_remaining,
        "reserved_shares": 0,
        "status": "OPEN",
        "created_at": now,
        "updated_at": now,
      }
    ]

  lots: List[Dict[str, Any]] = []
  for index, level in enumerate(eligible_sell_levels):
    if shares_remaining <= 0:
      break
    planned_shares = _int(level.get("planned_shares"))
    lot_shares = min(planned_shares, shares_remaining)
    is_last_level = index == len(eligible_sell_levels) - 1
    if is_last_level and shares_remaining > lot_shares:
      lot_shares = shares_remaining
    shares_remaining -= lot_shares
    lots.append(
      {
        "lot_id": f"initial-swing-{owner}-{level.get('grid_id') or index + 1}",
        "source_level_id": None,
        "source_level_index": None,
        "source": "INITIAL_SWING",
        "bucket": "swing",
        "entry_price": entry_price,
        "original_shares": lot_shares,
        "remaining_shares": lot_shares,
        "reserved_shares": 0,
        "target_sell_level_id": level.get("grid_id"),
        "target_sell_level_index": level.get("level_index"),
        "status": "OPEN",
        "created_at": now,
        "updated_at": now,
      }
    )
  return lots


def grid_book_to_template_snapshot(
  raw: Dict[str, Any],
  *,
  run_id: str,
  instrument_code: str = "",
  parameters: Optional[Dict[str, Any]] = None,
  needs_backtest: bool = False,
) -> Dict[str, Any]:
  """Copy a runtime/final GridBook into an editable planning template."""
  snapshot = normalize_grid_book(
    raw,
    run_id=run_id,
    instrument_code=instrument_code,
    parameters=parameters,
    editable=True,
    needs_backtest=needs_backtest,
  )
  updated_at = now_iso()
  levels = []
  for level in snapshot.get("levels") or []:
    item = dict(level or {})
    item["status"] = "DISABLED" if not item.get("enabled", True) else "PLANNED"
    item["monitoring"] = False
    item["pending_shares"] = 0
    item["filled_shares"] = 0
    item["reserved_inventory_shares"] = 0
    item["order_id"] = None
    item["entry_price"] = None
    item["entry_time"] = None
    item["last_intent_id"] = None
    item["last_trace_id"] = None
    item["reason"] = "template_copy"
    item["updated_at"] = updated_at
    levels.append(item)

  inventory_lots = []
  for lot in snapshot.get("inventory_lots") or []:
    item = dict(lot or {})
    item["reserved_shares"] = 0
    item["reserved_for_level_id"] = None
    item["reserved_order_id"] = None
    item["status"] = "OPEN" if _int(item.get("remaining_shares")) > 0 else "CLOSED"
    item["updated_at"] = updated_at
    inventory_lots.append(item)

  return normalize_grid_book(
    {
      **snapshot,
      "run_id": run_id,
      "instrument_code": instrument_code or snapshot.get("instrument_code"),
      "levels": levels,
      "inventory_lots": inventory_lots,
      "release_events": [],
      "editable": True,
      "needs_backtest": needs_backtest,
      "updated_at": updated_at,
    },
    run_id=run_id,
    instrument_code=instrument_code,
    parameters=parameters,
    editable=True,
    needs_backtest=needs_backtest,
  )


def grid_book_levels_to_parameters(levels: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
  rows = []
  for level in levels or []:
    normalized = normalize_level(dict(level or {}))
    rows.append(
      {
        "id": normalized["grid_id"],
        "gridId": normalized["grid_id"],
        "levelIndex": normalized["level_index"],
        "side": normalized["side"],
        "role": normalized["role"],
        "price": normalized["price"],
        "shares": normalized["planned_shares"],
        "amount": normalized["amount"],
        "pctFromBase": normalized["pct_from_base"],
        "expectedProfit": normalized["expected_profit"],
        "enabled": normalized["enabled"],
        "cycleCount": normalized["cycle_count"],
      }
    )
  return rows
