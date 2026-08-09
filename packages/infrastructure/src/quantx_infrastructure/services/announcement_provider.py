"""免费公告数据源适配。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Tuple

EASTMONEY_NOTICE_URL = "https://data.eastmoney.com/notices/"
EASTMONEY_REPURCHASE_URL = "https://data.eastmoney.com/gphg/"


@dataclass
class AnnouncementRecord:
  stock_code: str
  stock_name: Optional[str]
  title: str
  announcement_type: Optional[str]
  announce_date: Optional[date]
  source: str
  source_url: Optional[str]
  pdf_url: Optional[str]
  is_repurchase_related: bool
  raw_payload: Dict[str, Any]


@dataclass
class RepurchaseRecord:
  stock_code: str
  stock_name: Optional[str]
  source: str
  source_url: Optional[str]
  latest_announce_date: Optional[date]
  progress_status: Optional[str]
  price_floor: Optional[Decimal]
  price_ceiling: Optional[Decimal]
  planned_quantity_lower: Optional[Decimal]
  planned_quantity_average: Optional[Decimal]
  planned_quantity_upper: Optional[Decimal]
  planned_amount_lower: Optional[Decimal]
  planned_amount_upper: Optional[Decimal]
  repurchased_quantity: Optional[Decimal]
  repurchased_amount: Optional[Decimal]
  repurchased_ratio: Optional[Decimal]
  raw_payload: Dict[str, Any]


def normalize_internal_stock_code(value: str) -> str:
  """Normalize common A-share symbol forms to QuantX internal form."""

  raw = (value or "").strip().upper()
  if not raw:
    return ""

  raw = raw.replace("_", ".")
  prefix_match = re.fullmatch(r"(SH|SZ|BJ)\.?(\d{6})", raw)
  if prefix_match:
    market, digits = prefix_match.groups()
    return f"{digits}.{market}"

  suffix_match = re.fullmatch(r"(\d{6})\.?(SH|SZ|BJ)", raw)
  if suffix_match:
    digits, market = suffix_match.groups()
    return f"{digits}.{market}"

  digits_match = re.search(r"(\d{6})", raw)
  if not digits_match:
    return raw

  digits = digits_match.group(1)
  if digits.startswith(("6", "9")):
    market = "SH"
  elif digits.startswith(("4", "8")):
    market = "BJ"
  else:
    market = "SZ"
  return f"{digits}.{market}"


def to_akshare_security(value: str) -> str:
  normalized = normalize_internal_stock_code(value)
  match = re.search(r"(\d{6})", normalized)
  return match.group(1) if match else normalized


class AkshareAnnouncementProvider:
  """AkShare-backed announcement provider.

  The provider is intentionally synchronous because AkShare exposes blocking
  pandas APIs. Callers should run it in a worker thread from async services.
  """

  def __init__(self):
    self._akshare = None

  def fetch_announcements(
    self,
    stock_code: str,
    *,
    begin_date: str,
    end_date: str,
  ) -> List[AnnouncementRecord]:
    ak = self._load_akshare()
    security = to_akshare_security(stock_code)
    normalized_code = normalize_internal_stock_code(stock_code)

    try:
      df = ak.stock_individual_notice_report(
        security=security,
        symbol="全部",
        begin_date=begin_date,
        end_date=end_date,
      )
      records = [
        self._announcement_from_eastmoney_row(normalized_code, row)
        for row in _frame_records(df)
      ]
      records = [item for item in records if item.title]
      if records:
        return records
    except Exception:
      # Fall back to CNINFO below; the service records the final failure if both fail.
      pass

    df = ak.stock_zh_a_disclosure_report_cninfo(
      symbol=security,
      market="沪深京",
      keyword="",
      category="",
      start_date=begin_date,
      end_date=end_date,
    )
    return [
      item
      for item in (
        self._announcement_from_cninfo_row(normalized_code, row)
        for row in _frame_records(df)
      )
      if item.title
    ]

  def fetch_repurchase_events(self, stock_code: str) -> List[RepurchaseRecord]:
    ak = self._load_akshare()
    security = to_akshare_security(stock_code)
    normalized_code = normalize_internal_stock_code(stock_code)
    df = ak.stock_repurchase_em()
    records = []
    for row in _frame_records(df):
      row_security = str(_row_get(row, "股票代码", "代码") or "").strip()
      if row_security != security:
        continue
      records.append(self._repurchase_from_row(normalized_code, row))
    return records

  def _load_akshare(self):
    if self._akshare is not None:
      return self._akshare
    try:
      import akshare as ak  # type: ignore
    except Exception as exc:
      raise RuntimeError("AKShare 未安装或不可用") from exc
    self._akshare = ak
    return ak

  def _announcement_from_eastmoney_row(
    self,
    stock_code: str,
    row: Dict[str, Any],
  ) -> AnnouncementRecord:
    payload = _json_safe(row)
    title = str(_row_get(row, "公告标题") or "").strip()
    announcement_type = _optional_str(_row_get(row, "公告类型"))
    source_url = _optional_str(_row_get(row, "网址", "公告链接"))
    return AnnouncementRecord(
      stock_code=stock_code,
      stock_name=_optional_str(_row_get(row, "名称", "简称", "股票简称")),
      title=title,
      announcement_type=announcement_type,
      announce_date=_parse_date(_row_get(row, "公告日期", "公告时间")),
      source="EASTMONEY_AKSHARE",
      source_url=source_url,
      pdf_url=_pick_pdf_url(source_url),
      is_repurchase_related=_is_repurchase_related(title, announcement_type),
      raw_payload=payload,
    )

  def _announcement_from_cninfo_row(
    self,
    stock_code: str,
    row: Dict[str, Any],
  ) -> AnnouncementRecord:
    payload = _json_safe(row)
    title = str(_row_get(row, "公告标题") or "").strip()
    source_url = _optional_str(_row_get(row, "公告链接", "网址"))
    return AnnouncementRecord(
      stock_code=stock_code,
      stock_name=_optional_str(_row_get(row, "简称", "名称", "股票简称")),
      title=title,
      announcement_type=None,
      announce_date=_parse_date(_row_get(row, "公告时间", "公告日期")),
      source="CNINFO_AKSHARE",
      source_url=source_url,
      pdf_url=_pick_pdf_url(source_url),
      is_repurchase_related=_is_repurchase_related(title, None),
      raw_payload=payload,
    )

  def _repurchase_from_row(
    self,
    stock_code: str,
    row: Dict[str, Any],
  ) -> RepurchaseRecord:
    payload = _json_safe(row)
    price_floor, price_ceiling = _range_bounds(
      _row_get(row, "计划回购价格区间", "回购价格区间")
    )
    amount_floor, amount_ceiling = _range_bounds(
      _row_get(row, "计划回购金额区间", "拟回购金额区间")
    )
    source_url = _optional_str(_row_get(row, "网址", "公告链接"))
    return RepurchaseRecord(
      stock_code=stock_code,
      stock_name=_optional_str(_row_get(row, "股票简称", "名称", "简称")),
      source="EASTMONEY_AKSHARE",
      source_url=source_url or EASTMONEY_REPURCHASE_URL,
      latest_announce_date=_parse_date(
        _row_get(row, "最新公告日期", "公告日期", "首次公告日期")
      ),
      progress_status=_optional_str(_row_get(row, "实施进度", "回购进度")),
      price_floor=price_floor,
      price_ceiling=price_ceiling,
      planned_quantity_lower=_to_decimal(
        _row_get(row, "计划回购数量区间-下限", "计划回购数量下限")
      ),
      planned_quantity_average=_to_decimal(
        _row_get(row, "计划回购数量区间-平均", "计划回购数量平均")
      ),
      planned_quantity_upper=_to_decimal(
        _row_get(row, "计划回购数量区间-上限", "计划回购数量上限")
      ),
      planned_amount_lower=_to_decimal(
        _row_get(row, "计划回购金额区间-下限", "计划回购金额下限")
      )
      or amount_floor,
      planned_amount_upper=_to_decimal(
        _row_get(row, "计划回购金额区间-上限", "计划回购金额上限")
      )
      or amount_ceiling,
      repurchased_quantity=_to_decimal(
        _row_get(row, "已回购数量", "累计回购数量", "回购数量")
      ),
      repurchased_amount=_to_decimal(
        _row_get(row, "已回购金额", "累计回购金额", "回购金额")
      ),
      repurchased_ratio=_to_decimal(
        _row_get(row, "已回购股份占总股本比例", "占总股本比例", "回购比例")
      ),
      raw_payload=payload,
    )


def _frame_records(frame: Any) -> List[Dict[str, Any]]:
  if frame is None:
    return []
  if hasattr(frame, "to_dict"):
    return list(frame.to_dict("records"))
  if isinstance(frame, list):
    return [dict(item) for item in frame if isinstance(item, dict)]
  return []


def _row_get(row: Dict[str, Any], *names: str) -> Any:
  for name in names:
    if name in row:
      return row[name]
  return None


def _optional_str(value: Any) -> Optional[str]:
  if _is_empty(value):
    return None
  text = str(value).strip()
  return text or None


def _parse_date(value: Any) -> Optional[date]:
  if _is_empty(value):
    return None
  if isinstance(value, datetime):
    return value.date()
  if isinstance(value, date):
    return value
  text = str(value).strip()
  if not text:
    return None
  try:
    return datetime.fromisoformat(text).date()
  except ValueError:
    pass
  for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
    try:
      return datetime.strptime(text[: len(pattern)], pattern).date()
    except ValueError:
      continue
  return None


def _to_decimal(value: Any) -> Optional[Decimal]:
  if _is_empty(value):
    return None
  if isinstance(value, Decimal):
    return value
  text = str(value).strip().replace(",", "")
  match = re.search(r"-?\d+(?:\.\d+)?", text)
  if not match:
    return None
  try:
    return Decimal(match.group(0))
  except (InvalidOperation, ValueError):
    return None


def _range_bounds(value: Any) -> Tuple[Optional[Decimal], Optional[Decimal]]:
  if _is_empty(value):
    return None, None
  text = str(value).strip().replace(",", "")
  numbers = [Decimal(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
  if not numbers:
    return None, None
  if len(numbers) == 1:
    if "不超过" in text or "上限" in text or "以下" in text:
      return None, numbers[0]
    return numbers[0], numbers[0]
  return min(numbers), max(numbers)


def _json_safe(value: Any) -> Any:
  if isinstance(value, dict):
    return {str(key): _json_safe(item) for key, item in value.items()}
  if isinstance(value, (list, tuple, set)):
    return [_json_safe(item) for item in value]
  if isinstance(value, (datetime, date)):
    return value.isoformat()
  if isinstance(value, Decimal):
    return float(value)
  if _is_empty(value):
    return None
  if hasattr(value, "item"):
    try:
      return _json_safe(value.item())
    except Exception:
      pass
  return value


def _is_empty(value: Any) -> bool:
  if value is None:
    return True
  try:
    if isinstance(value, float) and math.isnan(value):
      return True
  except TypeError:
    pass
  text = str(value)
  return text in ("NaT", "nan", "None")


def _is_repurchase_related(title: str, announcement_type: Optional[str]) -> bool:
  haystack = f"{title or ''} {announcement_type or ''}"
  return "回购" in haystack


def _pick_pdf_url(url: Optional[str]) -> Optional[str]:
  if not url:
    return None
  if ".pdf" in url.lower():
    return url
  return None


def unique_stock_codes(values: Iterable[str]) -> List[str]:
  seen = set()
  result = []
  for value in values:
    code = normalize_internal_stock_code(value)
    if not code or code in seen:
      continue
    seen.add(code)
    result.append(code)
  return result
