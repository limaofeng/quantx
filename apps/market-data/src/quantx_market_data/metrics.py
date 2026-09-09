"""Metrics owned by the independent market Gateway process."""

from prometheus_client import Counter, Gauge, Histogram

MARKET_STREAM_CONNECTIONS = Gauge(
  "quantx_market_stream_connections",
  "Active dedicated QMT Agent market connections",
)

MARKET_STREAM_FRAMES = Counter(
  "quantx_market_stream_frames_total",
  "Whole-market frames accepted by Gateway",
  ["kind"],
)

MARKET_STREAM_RESYNCS = Counter(
  "quantx_market_stream_resyncs_total",
  "Whole-market streams invalidated for convergence",
  ["reason"],
)

MARKET_STREAM_PROCESSING = Histogram(
  "quantx_market_stream_processing_seconds",
  "Gateway validation plus Redis cache/publish time",
)

MARKET_STREAM_FRAME_BYTES = Gauge(
  "quantx_market_stream_frame_bytes",
  "Last whole-market binary frame size",
)

MARKET_STREAM_SEQUENCE = Gauge(
  "quantx_market_stream_sequence",
  "Last whole-market sequence committed by Gateway",
)

MARKET_STREAM_INSTRUMENTS = Gauge(
  "quantx_market_stream_instruments",
  "Instrument count in the last whole-market frame",
)

MARKET_STREAM_EVENTS = Counter(
  "quantx_market_stream_events_total",
  "Auxiliary market stream events",
  ["event", "reason"],
)
