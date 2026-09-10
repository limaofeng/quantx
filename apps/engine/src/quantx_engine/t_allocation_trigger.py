"""Bounded pending allocation triggers; never buffers or reduces market ticks."""

from dataclasses import dataclass

ALLOCATION_TRIGGER_DELAY_SECONDS = 0.1


@dataclass(frozen=True)
class AllocationTrigger:
  capture: object
  first_fence: int
  count: int = 1

  def merge(self, capture):
    if (
      capture.stream_id != self.capture.stream_id
      or capture.continuity_generation != self.capture.continuity_generation
      or capture.fence_sequence < self.capture.fence_sequence
    ):
      raise ValueError("LIVE_ALLOCATION_TRIGGER_IDENTITY_CHANGED")
    return AllocationTrigger(capture, self.first_fence, self.count + 1)

  def evidence(self):
    return {
      "stream_id": self.capture.stream_id,
      "continuity_generation": self.capture.continuity_generation,
      "first_fence": self.first_fence,
      "last_fence": self.capture.fence_sequence,
      "trigger_count": self.count,
      "coalescing_window_ms": int(ALLOCATION_TRIGGER_DELAY_SECONDS * 1000),
    }
