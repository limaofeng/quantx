import { type GridConfig, type GridResult } from '../types';

function priceRange(result: GridResult) {
  const prices = result.levels.map(level => level.price);
  if (prices.length === 0) return '未生成有效网格';
  return `${Math.min(...prices).toFixed(2)} - ${Math.max(...prices).toFixed(2)}`;
}

export function buildGridAuditPrompt(config: GridConfig, result: GridResult) {
  const buyLevels = result.levels.filter(level => level.side === 'BUY');
  const sellLevels = result.levels.filter(level => level.side === 'SELL');

  return `请作为 QuantX A 股策略研究助手，审计下面这份由当前页面生成的网格方案。只根据给定数据分析；如需补充实时行情，请明确说明并使用获准的只读工具。不要创建任务、修改策略或执行交易。

## 网格配置
- 标的：${config.symbol || '未填写'}
- 基准价：${config.basePrice.toFixed(2)}
- 网格类型：${config.gridType}
- 上行步长：${config.stepPctUp.toFixed(2)}%
- 下行步长：${config.stepPctDown.toFixed(2)}%
- 上行档数：${config.nUp}
- 下行档数：${config.nDown}
- 资金总额：${config.cashTotal.toFixed(2)}
- 当前持仓：${config.positionShares} 股
- 持仓均价：${config.avgCost.toFixed(2)}
- 封存仓：${config.lockedCoreShares} 股
- 核心仓：${config.coreShares} 股
- 活跃仓：${config.swingShares} 股
- 最大仓位比例：${config.maxPositionValuePct.toFixed(2)}%
- 买入预算比例：${config.buyBudgetPct.toFixed(2)}%
- 最小单笔金额：${config.minTradeValue.toFixed(2)}

## 计算结果
- 校验状态：${result.isValid ? '有效' : `无效（${result.errors.join('；') || '原因未知'}）`}
- 买入档数：${buyLevels.length}
- 卖出档数：${sellLevels.length}
- 网格价格区间：${priceRange(result)}
- 当前投入金额：${result.guards.totalInvested.toFixed(2)}
- 最大仓位金额：${result.guards.maxPositionValue.toFixed(2)}
- 计划买入预算：${result.guards.buyBudget.toFixed(2)}

请用简体中文输出：
1. 风险评分（1-10，10 为最高风险）及一句话结论；
2. 对网格密度、上下行不对称性、资金占用和 A 股交易约束的分析；
3. 三条针对当前参数的可执行优化建议；
4. 明确指出判断所缺少的数据，不得把假设写成事实。`;
}
