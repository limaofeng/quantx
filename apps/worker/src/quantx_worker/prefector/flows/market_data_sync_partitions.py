"""Bound historical Tick transfers and expose missing source partitions."""

from datetime import datetime


def plan_tick_partitions(codes, days, start, end, periods):
  first = datetime.strptime(start, "%Y%m%d").date()
  last = datetime.strptime(end, "%Y%m%d").date()
  if not days or days != sorted(set(days)) or min(days) < first or max(days) > last:
    raise ValueError("Tick 同步交易日历为空或超出请求区间")
  # One code/day limits payload size and identifies provider history gaps exactly.
  # Other selected periods use the same scope, avoiding duplicate overlapping reads.
  if "tick" not in periods or not codes or len(codes) != len(set(codes)):
    raise ValueError("Tick 同步标的或周期无效")
  return [
    ([code], day.strftime("%Y%m%d"), day.strftime("%Y%m%d"))
    for day in days
    for code in sorted(codes)
  ]


def validate_tick_partition(transfer, codes, periods, start, end):
  summaries = transfer.get("code_summaries") or []
  expected = {(code, period) for code in codes for period in periods}
  actual = {(item["code"], item["period"]) for item in summaries}
  if actual != expected or len(summaries) != len(expected):
    raise RuntimeError("Tick 同步缺少完整的标的/周期入库摘要")
  empty = [
    f"{item['code']}/{item['period']}" for item in summaries if item["row_count"] <= 0
  ]
  if empty:
    raise RuntimeError(
      f"行情源未返回数据: {start}..{end} {', '.join(empty)}; "
      f"request_id={transfer.get('request_id')}。请检查历史可用范围或停牌情况。"
    )
