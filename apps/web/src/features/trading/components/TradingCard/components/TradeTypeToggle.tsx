interface TradeTypeToggleProps {
  tradeType: 'buy' | 'sell';
  onTradeTypeChange: (type: 'buy' | 'sell') => void;
}

/**
 * 买入/卖出切换组件
 */
export function TradeTypeToggle({
  tradeType,
  onTradeTypeChange,
}: TradeTypeToggleProps) {
  return (
    <div className="flex border-b border-border mb-6">
      <button
        className={`px-ui-section py-2 font-medium border-b-2 transition-colors ${
          tradeType === 'buy'
            ? 'border-primary text-primary'
            : 'border-transparent text-muted-foreground hover:text-foreground'
        }`}
        onClick={() => onTradeTypeChange('buy')}
        data-testid="trade-type-buy"
      >
        买入
      </button>
      <button
        className={`px-ui-section py-2 font-medium border-b-2 transition-colors ${
          tradeType === 'sell'
            ? 'border-primary text-primary'
            : 'border-transparent text-muted-foreground hover:text-foreground'
        }`}
        onClick={() => onTradeTypeChange('sell')}
        data-testid="trade-type-sell"
      >
        卖出
      </button>
    </div>
  );
}
