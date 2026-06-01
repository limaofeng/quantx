// 市场状态组件
import { Card } from '@/components/ui/card';

export function MarketStatus() {
  // Temporary placeholder data until real API is connected
  const marketIndices = [
    { name: '上证指数', value: '3,050.12', change: '+1.2%', isPositive: true },
    { name: '深证成指', value: '9,850.55', change: '+0.8%', isPositive: true },
    { name: '创业板指', value: '1,950.33', change: '-0.5%', isPositive: false },
  ];

  return (
    <Card className="p-6">
      <h3 className="text-lg font-semibold mb-4">市场状态</h3>
      <div className="space-y-3">
        {marketIndices.map(index => (
          <div key={index.name} className="flex justify-between items-center">
            <span className="text-muted-foreground">{index.name}</span>
            <div className="text-right">
              <span
                className="font-medium"
                data-testid={`market-${index.name}-value`}
              >
                {index.value}
              </span>
              <span
                className={`text-sm ml-2 ${index.isPositive ? 'text-success' : 'text-destructive'}`}
                data-testid={`market-${index.name}-change`}
              >
                {index.change}
              </span>
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}
