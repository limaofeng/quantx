"""Market-data failure types without loading a broker or Windows SDK."""


class XTDataUnavailableError(RuntimeError):
  """The local XTData service is not ready for a data operation."""
