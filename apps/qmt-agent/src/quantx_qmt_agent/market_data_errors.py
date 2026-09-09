"""Market-data failure types without loading a broker or Windows SDK."""


class XTDataUnavailableError(RuntimeError):
  """The local XTData service is not ready for a data operation."""


class HistoricalDataUnavailableError(ValueError):
  """The source returned no usable data for part of the requested scope."""
