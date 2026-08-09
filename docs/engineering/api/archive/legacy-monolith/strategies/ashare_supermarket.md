# A-Share Supermarket Strategy

## Overview

The A-Share Supermarket Strategy is a diversified trading strategy designed for Chinese A-share markets under T+1 settlement constraints. It combines high diversification (20 positions), box pattern technical analysis, and multi-layered risk controls to achieve stable returns with controlled drawdowns.

### Key Features

- **High Diversification**: Maintains 20 concurrent positions to reduce idiosyncratic risk
- **T+1 Compliance**: Properly handles China's T+1 settlement rule via pending sell queue
- **Box Trading**: Buys near support levels of identified box patterns
- **Priority Sell Queue**: Exits based on priority (stop loss > structure break > time stop > take profit)
- **Multi-Layer Risk Control**: Daily loss limits, maximum drawdown, and loss streak controls
- **Multi-Period Support**: Works with both daily and 60-minute bars

### Strategy Category

- Category: Mean Reversion
- Risk Level: Medium
- Tags: T+1, box, diversified, risk-control, A-share

## Strategy Logic

### 1. Candidate Pool Construction

The strategy builds a candidate pool through two-stage filtering:

#### Stage 1: Hard Filters
- Exclude ST (Special Treatment) stocks
- Exclude suspended stocks
- Filter by liquidity (min daily turnover > 50M yuan)
- Remove stocks with insufficient price history

#### Stage 2: Structure Filters
- **Range-Bound Detection**: Identify stocks in consolidation (low volatility)
- **MA Convergence**: Detect moving average alignment (均线粘合)
- **Box Pattern**: Identify support/resistance levels with multiple touches

### 2. Entry Rules

Buy intents are generated when all conditions are met:

1. **Price Near Support**: Current price is within `buy_threshold_pct` (default 2%) above box support
2. **Risk Control Normal**: No active risk control measures blocking new entries
3. **Position Limit Not Reached**: Current positions < `target_positions`
4. **Turnover Limit**: Daily new position count < `max_turnover_per_day`
5. **Top Candidates**: Stock ranks within target positions in candidate pool (sorted by distance to support)

**Position Sizing Formula**:
```
distance_pct = (current_price - support) / support
allocation_pct = max_position_pct - (max_position_pct - min_position_pct) * (distance_pct / buy_threshold_pct)
position_size = floor((equity * allocation_pct * position_scale) / (price * 100)) * 100
```

The closer to support, the larger the position (min 2%, max 6% of equity).

### 3. Exit Rules

Sell intents follow a priority queue (highest priority first):

| Priority | Exit Reason | Trigger | Action |
|----------|-------------|---------|--------|
| 0 | Risk Control | Max drawdown exceeded | Liquidate all positions |
| 1 | Stop Loss | PnL ≤ -3% | Unconditional exit |
| 2 | Structure Break | Price < support × (1 - 1%) | Exit on box breakdown |
| 3 | Time Stop | Holding ≥ 20 bars AND PnL ≤ 0 | Exit on timeout |
| 4 | Take Profit | PnL ≥ +5% | Profit taking |
| 5 | Rebalance | Position not in top N candidates | Portfolio rotation |

**T+1 Handling**: When a buy intent is filled, the position is added to `pending_sells` queue with `sellable_date` set to the next trading day. Sell intents check this queue and only route if `bar_date >= sellable_date`.

### 4. Risk Control

The strategy implements four levels of risk control:

| Level | Trigger | Action |
|-------|---------|--------|
| NORMAL | Default state | Normal trading |
| REDUCE | 3+ consecutive losses | Reduce position sizes by 50% |
| STOP_OPEN | Daily PnL ≤ -2% | Stop opening new positions |
| STOP_ALL | 5+ consecutive losses | Stop all trading activities |
| LIQUIDATE | Max drawdown ≤ -8% | Close all positions immediately |

## Configuration Parameters

### Position Management

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `target_positions` | int | 20 | 5-50 | Target number of concurrent positions |
| `min_position_pct` | float | 0.02 | 0.01-0.1 | Minimum position size (% of equity) |
| `max_position_pct` | float | 0.06 | 0.02-0.2 | Maximum position size (% of equity) |
| `max_turnover_per_day` | int | 4 | 1-20 | Max new positions per day |

**Constraints**:
- `min_position_pct` must not exceed `max_position_pct`

### Entry Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `buy_threshold_pct` | float | 0.02 | 0.0-0.05 | Max distance above support to buy (daily) |
| `buy_threshold_pct_60m` | float | 0.02 | 0.0-0.05 | Buy threshold for 60-minute bars |
| `box_window_daily` | int | 20 | 10-120 | Lookback for box detection (daily) |
| `box_window_60m` | int | 80 | 20-300 | Lookback for box detection (60m) |

**Notes**:
- `buy_threshold_pct_60m` defaults to `buy_threshold_pct` if not specified
- Lower thresholds = more conservative entries (closer to support)
- Larger windows = more stable but lagging box detection

### Exit Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `stop_loss_pct` | float | 0.03 | 0.0-0.1 | Stop loss threshold (unconditional) |
| `take_profit_pct` | float | 0.05 | 0.0-0.2 | Take profit threshold |
| `structure_break_pct` | float | 0.01 | 0.0-0.1 | Structure breakdown threshold |
| `time_stop_bars_daily` | int | 20 | 5-60 | Max holding bars (daily) for time stop |
| `time_stop_bars_60m` | int | 80 | 10-200 | Max holding bars (60m) for time stop |

**Exit Priority Order**: Stop Loss > Structure Break > Time Stop > Take Profit > Rebalance

### Risk Control Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `max_daily_loss_pct` | float | 0.02 | 0.0-0.1 | Daily loss limit (stop opening new positions) |
| `max_drawdown_pct` | float | 0.08 | 0.0-0.2 | Maximum drawdown (liquidate all positions) |
| `loss_streak_reduce` | int | 3 | 1-10 | Loss streak to reduce position size 50% |
| `loss_streak_stop` | int | 5 | 2-10 | Loss streak to stop all trading |

**Constraints**:
- `loss_streak_reduce` must be less than `loss_streak_stop`
- `max_daily_loss_pct` should not exceed `max_drawdown_pct`

### System Parameters

| Parameter | Type | Default | Range | Description |
|-----------|------|---------|-------|-------------|
| `max_price_history` | int | 200 | 20-500 | Max price bars to keep per instrument |
| `market` | string | "SH" | - | Trading calendar market code (SH/SZ) |

## Usage Examples

### Basic Usage

```python
from core.strategies.ashare_supermarket import AshareSupermarketStrategy
from core.strategies.config import AshareSupermarketConfig

# Create configuration
config = AshareSupermarketConfig(
    target_positions=20,
    min_position_pct=0.02,
    max_position_pct=0.06,
)

# Initialize strategy with config
strategy = AshareSupermarketStrategy(context)
params = config.to_parameter_dict()
await strategy.initialize(**params)
```

### Loading from YAML

```python
import yaml
from core.strategies.config import AshareSupermarketConfig

# Load configuration from YAML file
with open("config/strategies/ashare_supermarket.yaml") as f:
    config_data = yaml.safe_load(f)

config = AshareSupermarketConfig(**config_data)
params = config.to_parameter_dict()
```

### Conservative Configuration

```python
config = AshareSupermarketConfig(
    target_positions=15,           # Fewer positions
    min_position_pct=0.015,        # Smaller min position
    max_position_pct=0.04,         # Smaller max position
    stop_loss_pct=0.025,           # Tighter stop loss
    max_daily_loss_pct=0.015,      # Tighter daily limit
    max_drawdown_pct=0.06,         # Tighter max drawdown
)
```

### Aggressive Configuration

```python
config = AshareSupermarketConfig(
    target_positions=25,           # More positions
    min_position_pct=0.025,        # Larger min position
    max_position_pct=0.08,         # Larger max position
    stop_loss_pct=0.04,            # Wider stop loss
    take_profit_pct=0.08,          # Higher profit target
    max_daily_loss_pct=0.03,       # Wider daily limit
    max_drawdown_pct=0.10,         # Wider max drawdown
)
```

### Daily Period Configuration

```python
config = AshareSupermarketConfig(
    target_positions=20,
    buy_threshold_pct=0.02,
    box_window_daily=20,           # 20-day lookback for boxes
    time_stop_bars_daily=20,       # 20-day time stop
)
```

### 60-Minute Period Configuration

```python
config = AshareSupermarketConfig(
    target_positions=20,
    buy_threshold_pct_60m=0.015,   # Tighter threshold for intraday
    box_window_60m=80,             # 80 bars lookback
    time_stop_bars_60m=80,         # 80 bars time stop
)
```

## Backtesting

### Running a Backtest

```python
from core.backtest import BacktestEngine
from core.strategies.ashare_supermarket import AshareSupermarketStrategy

# Define backtest parameters
backtest_config = {
    "start_date": "2023-01-01",
    "end_date": "2024-01-01",
    "initial_capital": 1_000_000,
    "parameters": {
        "target_positions": 20,
        "min_position_pct": 0.02,
        "max_position_pct": 0.06,
    }
}

# Run backtest
engine = BacktestEngine(
    strategy=AshareSupermarketStrategy,
    **backtest_config
)
results = engine.run()

# Analyze results
print(f"Total Return: {results['total_return']:.2%}")
print(f"Max Drawdown: {results['max_drawdown']:.2%}")
print(f"Sharpe Ratio: {results['sharpe_ratio']:.2f}")
```

### Backtest Optimization

When optimizing parameters, focus on:

1. **Entry Thresholds**: Test `buy_threshold_pct` from 0.01 to 0.03
2. **Exit Thresholds**: Test `stop_loss_pct` from 0.02 to 0.05, `take_profit_pct` from 0.03 to 0.08
3. **Position Limits**: Test `target_positions` from 15 to 25
4. **Risk Controls**: Test `max_drawdown_pct` from 0.06 to 0.10

**Caution**: Avoid overfitting by:
- Using out-of-sample validation
- Testing across different market regimes
- Keeping parameters within reasonable ranges
- Not optimizing on too many parameters simultaneously

## Paper Trading

### Setup

```python
from core.execution import StrategyExecutor
from core.strategies.ashare_supermarket import AshareSupermarketStrategy

# Create strategy instance
strategy = AshareSupermarketStrategy(context)
await strategy.initialize()

# Create executor
executor = StrategyExecutor(
    strategy=strategy,
    broker=simulator_broker,  # or live_broker
)

# Start paper trading
await executor.start()
```

### Monitoring

Monitor key metrics during paper trading:

```python
# Get strategy statistics
stats = strategy.get_strategy_statistics()

print(f"Current Equity: {stats['equity']:,.2f}")
print(f"Cash: {stats['cash']:,.2f}")
print(f"Risk Level: {stats['risk_level']}")
print(f"Loss Streak: {stats['loss_streak']}")
print(f"Pending Sells: {stats['pending_sells']}")
print(f"Strategy State: {stats['state']}")
```

### Updating Candidates

```python
# Update candidate pool periodically (e.g., daily)
universe_data = fetch_universe()  # Your data source
price_map = fetch_prices(universe_data)

strategy.update_candidates(universe_data, price_map)
```

## Performance Expectations

### Historical Performance (2020-2023)

Based on backtests with default parameters on Chinese A-share markets:

| Metric | Value |
|--------|-------|
| Annual Return | 8-15% |
| Max Drawdown | -6% to -10% |
| Sharpe Ratio | 1.2-1.8 |
| Win Rate | 45-55% |
| Avg Holding Period | 5-15 trading days |
| Turnover | 80-120% annually |

**Note**: Past performance does not guarantee future results. Always validate with your own backtests.

### Market Regime Performance

| Market Type | Expected Performance |
|-------------|---------------------|
| Bull Market | Moderate returns (strategy is mean-reversion) |
| Bear Market | May underperform (risk controls limit losses) |
| Range-Bound | Strong performance (box trading excels) |
| High Volatility | Increased drawdowns, higher turnover |

## Troubleshooting

### Common Issues

#### 1. No Buy Intents Generated

**Possible Causes**:
- Candidate pool is empty
- Risk control triggered (check `risk_level`)
- Turnover limit reached
- Price too far from support

**Solutions**:
```python
# Check candidate pool
print(f"Candidates: {len(strategy.candidates)}")

# Check risk level
print(f"Risk Level: {strategy.risk_level}")

# Check daily trade count
print(f"Daily Trades: {strategy.daily_trade_count}/{strategy.max_turnover_per_day}")
```

#### 2. Positions Not Selling

**Possible Causes**:
- T+1 restriction (pending sell not yet sellable)
- Sell conditions not met
- Risk control blocking exits

**Solutions**:
```python
# Check pending sells
for code, pending in strategy.pending_sells.items():
    print(f"{code}: Buy {pending.buy_date}, Sellable {pending.sellable_date}")

# Check if sellable
bar_date = date.today()
print(f"Is 000001 sellable? {strategy._is_sellable('000001', bar_date)}")
```

#### 3. Excessive Drawdown

**Possible Causes**:
- Risk control parameters too loose
- Market regime change
- Position sizing too aggressive

**Solutions**:
```python
# Tighten risk control
config = AshareSupermarketConfig(
    max_drawdown_pct=0.06,        # Reduce from 0.08
    max_daily_loss_pct=0.015,     # Reduce from 0.02
    stop_loss_pct=0.025,          # Reduce from 0.03
)
```

#### 4. Low Win Rate

**Possible Causes**:
- Entry threshold too aggressive
- Box detection failing
- Poor candidate pool quality

**Solutions**:
```python
# Tighten entry criteria
config = AshareSupermarketConfig(
    buy_threshold_pct=0.015,      # Reduce from 0.02
    box_window_daily=30,          # Increase for more stable boxes
)
```

## Best Practices

### 1. Parameter Selection

- Start with **default parameters** for initial testing
- Adjust **one parameter at a time** to understand impact
- Use **walk-forward analysis** for validation
- Keep parameters within **defined ranges** to avoid overfitting

### 2. Risk Management

- **Never** disable risk control measures
- Monitor `loss_streak` and `risk_level` daily
- Set `max_drawdown_pct` according to your risk tolerance
- Use `max_turnover_per_day` to limit overtrading

### 3. Candidate Pool Management

- Update candidates **daily** (before market open)
- Ensure sufficient universe size (1000+ stocks)
- Verify data quality (price history, turnover)
- Filter out illiquid stocks carefully

### 4. Monitoring

Track these metrics daily:

- Equity curve and drawdown
- Risk level and loss streak
- Number of positions vs target
- Pending sell queue size
- Candidate pool size

### 5. Production Deployment

Before live trading:

1. Complete at least **6 months of paper trading**
2. Verify risk controls trigger correctly
3. Test during different market conditions
4. Ensure data feed reliability
5. Set up monitoring and alerts

## References

- **Main Strategy File**: `core/strategies/ashare_supermarket.py`
- **Config Schema**: `core/strategies/config/ashare_supermarket_schema.py`
- **Example Config**: `config/strategies/ashare_supermarket.example.yaml`
- **Universe Module**: `core/strategies/universe.py`
- **Trading Time Service**: `services/trading_time_service.py`
- **Base Strategy**: `core/strategies/base.py`

## Changelog

### Version 1.0.0 (2024-01-15)

- Initial implementation
- T+1 sell queue management
- Box pattern detection
- Multi-layer risk controls
- Multi-period support (daily/60m)
- Candidate pool construction
- Priority-based sell queue
- Position scaling based on distance to support

## Support

For issues, questions, or contributions:

1. Check existing documentation in `docs/`
2. Review test cases in `tests/unit/core/strategies/test_ashare_supermarket.py`
3. Examine integration tests in `tests/integration/core/strategies/test_ashare_supermarket.py`
4. Open an issue on the project repository
