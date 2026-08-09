"""
A-share Supermarket Strategy Configuration Schema

Provides Pydantic-based validation for strategy parameters with type safety
and detailed validation rules.
"""

from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AshareSupermarketConfig(BaseModel):
    """Configuration schema for A-share Supermarket Strategy."""

    # Position Management
    target_positions: int = Field(
        default=20,
        ge=5,
        le=50,
        description="Target number of concurrent positions to maintain",
    )
    min_position_pct: float = Field(
        default=0.02,
        ge=0.01,
        le=0.1,
        description="Minimum allocation per position as percentage of equity",
    )
    max_position_pct: float = Field(
        default=0.06,
        ge=0.02,
        le=0.2,
        description="Maximum allocation per position as percentage of equity",
    )
    max_turnover_per_day: int = Field(
        default=4,
        ge=1,
        le=20,
        description="Maximum number of new positions per trading day",
    )

    # Entry Parameters
    buy_threshold_pct: float = Field(
        default=0.02,
        ge=0.0,
        le=0.05,
        description="Buy threshold: buy when price within this % above box support (daily)",
    )
    buy_threshold_pct_60m: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=0.05,
        description="Buy threshold for 60-minute bars (defaults to buy_threshold_pct)",
    )
    box_window_daily: int = Field(
        default=20,
        ge=10,
        le=120,
        description="Lookback window for box pattern detection on daily bars",
    )
    box_window_60m: int = Field(
        default=80,
        ge=20,
        le=300,
        description="Lookback window for box pattern detection on 60-minute bars",
    )

    # Exit Parameters
    stop_loss_pct: float = Field(
        default=0.03,
        ge=0.0,
        le=0.1,
        description="Stop loss percentage (unconditional exit)",
    )
    take_profit_pct: float = Field(
        default=0.05,
        ge=0.0,
        le=0.2,
        description="Take profit percentage",
    )
    structure_break_pct: float = Field(
        default=0.01,
        ge=0.0,
        le=0.1,
        description="Structure breakdown threshold (below box support)",
    )
    time_stop_bars_daily: int = Field(
        default=20,
        ge=5,
        le=60,
        description="Maximum holding bars (daily) before time stop when no profit",
    )
    time_stop_bars_60m: int = Field(
        default=80,
        ge=10,
        le=200,
        description="Maximum holding bars (60m) before time stop when no profit",
    )

    # Risk Control Parameters
    max_daily_loss_pct: float = Field(
        default=0.02,
        ge=0.0,
        le=0.1,
        description="Daily loss limit: stop opening new positions after this loss",
    )
    max_drawdown_pct: float = Field(
        default=0.08,
        ge=0.0,
        le=0.2,
        description="Maximum drawdown: liquidate all positions when exceeded",
    )
    loss_streak_reduce: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Loss streak count to trigger 50% position size reduction",
    )
    loss_streak_stop: int = Field(
        default=5,
        ge=2,
        le=10,
        description="Loss streak count to stop opening new positions entirely",
    )

    # System Parameters
    max_price_history: int = Field(
        default=200,
        ge=20,
        le=500,
        description="Maximum number of price bars to keep in history per instrument",
    )
    market: str = Field(
        default="SH",
        description="Trading calendar market code (SH, SZ, etc.)",
    )

    @field_validator("buy_threshold_pct_60m")
    @classmethod
    def validate_buy_threshold_60m(cls, v: Optional[float], info) -> Optional[float]:
        """Set 60m buy threshold to daily value if not provided."""
        if v is None:
            return info.data.get("buy_threshold_pct", 0.02)
        return v

    @model_validator(mode="after")
    def validate_position_constraints(self) -> "AshareSupermarketConfig":
        """Validate position allocation constraints."""
        if self.min_position_pct > self.max_position_pct:
            raise ValueError(
                f"min_position_pct ({self.min_position_pct}) cannot exceed "
                f"max_position_pct ({self.max_position_pct})"
            )
        return self

    @model_validator(mode="after")
    def validate_loss_streak_constraints(self) -> "AshareSupermarketConfig":
        """Validate loss streak thresholds."""
        if self.loss_streak_reduce >= self.loss_streak_stop:
            raise ValueError(
                f"loss_streak_reduce ({self.loss_streak_reduce}) must be less than "
                f"loss_streak_stop ({self.loss_streak_stop})"
            )
        return self

    @model_validator(mode="after")
    def validate_risk_constraints(self) -> "AshareSupermarketConfig":
        """Validate risk control parameters consistency."""
        if self.max_daily_loss_pct > self.max_drawdown_pct:
            raise ValueError(
                f"max_daily_loss_pct ({self.max_daily_loss_pct}) should not exceed "
                f"max_drawdown_pct ({self.max_drawdown_pct})"
            )
        return self

    def to_parameter_dict(self) -> dict:
        """Convert config to dictionary format for strategy initialization."""
        return self.model_dump(exclude_none=True)

    @classmethod
    def from_parameter_dict(cls, params: dict) -> "AshareSupermarketConfig":
        """Create config from parameter dictionary."""
        return cls(**params)

    class Config:
        """Pydantic model configuration."""

        json_encoders = {float: lambda v: round(v, 4)}
        extra = "ignore"
        json_schema_extra = {
            "example": {
                "target_positions": 20,
                "min_position_pct": 0.02,
                "max_position_pct": 0.06,
                "buy_threshold_pct": 0.02,
                "stop_loss_pct": 0.03,
                "take_profit_pct": 0.05,
                "max_daily_loss_pct": 0.02,
                "max_drawdown_pct": 0.08,
            }
        }


class CandidatePoolConfig(BaseModel):
    """Configuration for candidate pool filtering."""

    # Hard Filters
    min_turnover: float = Field(
        default=50_000_000,
        ge=0,
        description="Minimum daily turnover in yuan for liquidity filter",
    )
    exclude_st: bool = Field(
        default=True,
        description="Exclude ST (special treatment) stocks",
    )
    exclude_suspended: bool = Field(
        default=True,
        description="Exclude suspended trading stocks",
    )

    # Structure Filters
    max_volatility_pct: Optional[float] = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Maximum historical volatility percentage for range-bound filter",
    )
    ma_divergence_pct: Optional[float] = Field(
        default=0.05,
        ge=0.0,
        le=0.2,
        description="Maximum MA divergence percentage for均线粘合 filter",
    )
    min_data_points: int = Field(
        default=60,
        ge=20,
        le=500,
        description="Minimum data points required for technical analysis",
    )

    # Box Detection
    min_box_width_pct: float = Field(
        default=0.05,
        ge=0.01,
        le=0.3,
        description="Minimum box width as percentage of price",
    )
    max_box_width_pct: float = Field(
        default=0.20,
        ge=0.05,
        le=0.5,
        description="Maximum box width as percentage of price",
    )
    min_touches: int = Field(
        default=2,
        ge=2,
        le=10,
        description="Minimum touches of support/resistance for valid box",
    )

    @model_validator(mode="after")
    def validate_box_width(self) -> "CandidatePoolConfig":
        """Validate box width constraints."""
        if self.min_box_width_pct > self.max_box_width_pct:
            raise ValueError(
                f"min_box_width_pct ({self.min_box_width_pct}) cannot exceed "
                f"max_box_width_pct ({self.max_box_width_pct})"
            )
        return self

    def to_parameter_dict(self) -> dict:
        """Convert config to dictionary format."""
        return self.model_dump(exclude_none=True)

    class Config:
        """Pydantic model configuration."""

        extra = "ignore"
