"""TradeIntent weighting and aggregation helpers."""

import logging
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from core.strategies.base import (
  TradeIntent,
  TradeIntentDirection,
  TradeIntentPriority,
)


class IntentStrength(Enum):
  WEAK = 1
  MODERATE = 2
  STRONG = 3
  VERY_STRONG = 4


@dataclass
class WeightedIntent:
  intent: TradeIntent
  strength: IntentStrength = IntentStrength.MODERATE
  weight: float = 1.0
  source: str = "unknown"


class IntentGenerator:
  def __init__(self, name: str, weight: float = 1.0):
    self.name = name
    self.weight = weight
    self.logger = logging.getLogger(f"IntentGen-{name}")
    self.intents_generated = 0

  def build_intent(
    self,
    *,
    strategy_id: str,
    run_id: str,
    instrument_code: str,
    direction: TradeIntentDirection,
    bucket: str,
    reason: str,
    target_amount: Optional[float] = None,
    target_position_pct: Optional[float] = None,
    limit_price_hint: Optional[float] = None,
    confidence: float = 1.0,
    priority: TradeIntentPriority = TradeIntentPriority.NORMAL,
    strength: IntentStrength = IntentStrength.MODERATE,
    metadata: Optional[Dict[str, Any]] = None,
  ) -> WeightedIntent:
    intent = TradeIntent(
      strategy_id=strategy_id,
      run_id=run_id,
      instrument_code=instrument_code,
      direction=direction,
      bucket=bucket,
      reason=reason,
      priority=priority,
      confidence=confidence,
      target_amount=target_amount,
      target_position_pct=target_position_pct,
      limit_price_hint=limit_price_hint,
      metadata=metadata or {},
    )
    self.intents_generated += 1
    return WeightedIntent(
      intent=intent,
      strength=strength,
      weight=self.weight,
      source=self.name,
    )

  def get_statistics(self) -> Dict[str, Any]:
    return {
      "name": self.name,
      "weight": self.weight,
      "intents_generated": self.intents_generated,
    }


class IntentAggregator:
  def __init__(self, name: str = "DefaultAggregator"):
    self.name = name
    self.generators: Dict[str, IntentGenerator] = {}
    self.intent_buffer: List[WeightedIntent] = []
    self.aggregation_rules: Dict[str, Any] = {
      "min_total_weight": 0.5,
      "max_buffer_size": 1000,
    }
    self.logger = logging.getLogger(f"IntentAgg-{name}")

  def register_generator(self, generator: IntentGenerator) -> None:
    self.generators[generator.name] = generator

  def add_intent(self, weighted_intent: WeightedIntent) -> None:
    self.intent_buffer.append(weighted_intent)
    if len(self.intent_buffer) > self.aggregation_rules["max_buffer_size"]:
      self.intent_buffer.pop(0)

  def aggregate(self, instrument_code: str) -> Optional[TradeIntent]:
    relevant = [
      weighted
      for weighted in self.intent_buffer
      if weighted.intent.instrument_code == instrument_code
    ]
    if not relevant:
      return None

    groups = defaultdict(list)
    for weighted in relevant:
      groups[weighted.intent.direction].append(weighted)

    best_direction = None
    best_score = 0.0
    for direction, weighted_intents in groups.items():
      score = sum(item.weight * item.intent.confidence * item.strength.value for item in weighted_intents)
      if score > best_score:
        best_score = score
        best_direction = direction

    if best_direction is None or best_score < self.aggregation_rules["min_total_weight"]:
      return None

    best = max(
      groups[best_direction],
      key=lambda item: item.intent.priority.value if hasattr(item.intent.priority, "value") else 0,
    )
    return best.intent

  def get_recent_intents(
    self, instrument_code: Optional[str] = None, count: int = 10
  ) -> List[WeightedIntent]:
    intents = self.intent_buffer
    if instrument_code:
      intents = [item for item in intents if item.intent.instrument_code == instrument_code]
    return sorted(intents, key=lambda item: item.intent.created_at, reverse=True)[:count]

  def get_statistics(self) -> Dict[str, Any]:
    counts = defaultdict(int)
    for weighted in self.intent_buffer:
      counts[weighted.intent.direction.value] += 1
    return {
      "name": self.name,
      "registered_generators": len(self.generators),
      "buffer_size": len(self.intent_buffer),
      "intent_counts": dict(counts),
      "generator_stats": {
        name: generator.get_statistics()
        for name, generator in self.generators.items()
      },
    }
