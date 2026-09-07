"""Shared classification of an exchange-local source time; no clock access."""

from datetime import datetime, time

from quantx_domain.strategies.base import MarketDataSession


def classify_market_data_session(value: datetime) -> MarketDataSession:
  current = value.time()
  if current < time(9, 15):
    return MarketDataSession.PRE_OPEN
  if current < time(9, 30):
    return MarketDataSession.OPENING_AUCTION
  if current <= time(11, 30):
    return MarketDataSession.CONTINUOUS_AM
  if current < time(13, 0):
    return MarketDataSession.LUNCH_BREAK
  if current < time(14, 57):
    return MarketDataSession.CONTINUOUS_PM
  if current <= time(15, 0):
    return MarketDataSession.CLOSING_AUCTION
  return MarketDataSession.CLOSED
