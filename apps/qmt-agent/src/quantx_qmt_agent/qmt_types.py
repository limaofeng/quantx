"""miniQMT-only adapter enums."""

from enum import Enum, IntEnum

from xtquant import xtconstant


class InstrumentType(str, Enum):
  INDEX = "index"
  STOCK = "stock"
  FUND = "fund"
  ETF = "etf"
  TRR = "trr"


class AccountType(str, Enum):
  STOCK = "STOCK"
  HUGANGTONG = "HUGANGTONG"
  CREDIT = "CREDIT"
  FUTURE = "FUTURE"
  SHENGANGTONG = "SHENGANGTONG"


class PriceType(IntEnum):
  FIX_PRICE = xtconstant.FIX_PRICE
  LATEST_PRICE = xtconstant.LATEST_PRICE
  MARKET_PEER_PRICE_FIRST = xtconstant.MARKET_PEER_PRICE_FIRST
  MARKET_MINE_PRICE_FIRST = xtconstant.MARKET_MINE_PRICE_FIRST
  MARKET_CONVERT_5_LIMIT = 40


class OrderPriceType(IntEnum):
  ANY = 49
  LIMIT = 50
  BEST = 51
  PROP_BUYBACK = 55


class OrderType(IntEnum):
  BUY = xtconstant.STOCK_BUY
  SELL = xtconstant.STOCK_SELL


class OrderStatus(IntEnum):
  UNREPORTED = xtconstant.ORDER_UNREPORTED
  WAIT_REPORTING = xtconstant.ORDER_WAIT_REPORTING
  REPORTED = xtconstant.ORDER_REPORTED
  REPORTED_CANCEL = xtconstant.ORDER_REPORTED_CANCEL
  PARTSUCC_CANCEL = xtconstant.ORDER_PARTSUCC_CANCEL
  PART_CANCEL = xtconstant.ORDER_PART_CANCEL
  CANCELED = xtconstant.ORDER_CANCELED
  PART_SUCC = xtconstant.ORDER_PART_SUCC
  SUCCEEDED = xtconstant.ORDER_SUCCEEDED
  JUNK = xtconstant.ORDER_JUNK
  UNKNOWN = xtconstant.ORDER_UNKNOWN
