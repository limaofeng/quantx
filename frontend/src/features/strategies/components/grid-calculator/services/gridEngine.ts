import {
  type GridConfig,
  type GridResult,
  type GridLevel,
  GridType,
} from '../types';

const roundToTick = (price: number): number => {
  // A-Share usually 0.01 precision
  return Math.round(price * 100) / 100;
};

export const generateGridStrategy = (config: GridConfig): GridResult => {
  const errors: string[] = [];
  const levels: GridLevel[] = [];

  // 1. Determine Base Price
  const basePrice = config.basePrice;

  // 2. Parsed Params
  const rDown = config.stepPctDown / 100;
  const rUp = config.stepPctUp / 100;
  const nDown = config.nDown;
  const nUp = config.nUp;
  const lotSize = 100; // Standard for A-Shares

  // 3. Budgets
  const attributedShares =
    Math.max(0, config.lockedCoreShares || 0) +
    Math.max(0, config.coreShares || 0) +
    Math.max(0, config.swingShares || 0);
  const currentPositionShares = Math.max(
    0,
    config.positionShares || attributedShares
  );
  const currentPosValue = currentPositionShares * config.basePrice;
  const maxPosValue = config.cashTotal * (config.maxPositionValuePct / 100);

  // Available cash for *new* buy grids
  const remainingCapacity = Math.max(0, maxPosValue - currentPosValue);

  // Specific budget user wants to allocate to the grid buying part
  const userBuyBudget = config.cashTotal * (config.buyBudgetPct / 100);
  const actualBuyBudget = Math.min(userBuyBudget, remainingCapacity);

  // 4. Generate Buy Levels (Down)
  const cashPerBuyGrid = nDown > 0 ? actualBuyBudget / nDown : 0;

  for (let k = 1; k <= nDown; k++) {
    let price = 0;
    let refPrice = 0;

    if (config.gridType === GridType.GEOMETRIC) {
      price = roundToTick(basePrice * Math.pow(1 - rDown, k));
      refPrice = roundToTick(basePrice * Math.pow(1 - rDown, k - 1));
    } else {
      price = roundToTick(basePrice * (1 - rDown * k));
      refPrice = roundToTick(basePrice * (1 - rDown * (k - 1)));
    }

    let shares = 0;
    if (price > 0) {
      shares = Math.floor(cashPerBuyGrid / price / lotSize) * lotSize;
    }

    const amount = shares * price;

    // Profit if bought at `price` and sold at `refPrice` (one grid up) - For Profit Calculation, usually we compare with immediate next UP grid
    // But in asymmetric grids, the Up Grid Spacing might differ.
    // However, the traditional 'Gap' profit is often conceptualized around the step size of that specific grid leg.
    // Let's stick to using the 'refPrice' (price 1 step closer to base) as the target for this specific buy order's generated profit.
    const expectedProfit = Math.max(0, (refPrice - price) * shares);

    // Check constraints
    if (amount < config.minTradeValue && amount > 0) {
      shares = 0;
    }

    if (shares > 0) {
      levels.push({
        id: `buy-${k}`,
        levelIndex: -k,
        side: 'BUY',
        price,
        shares,
        amount,
        pctFromBase: -(((basePrice - price) / basePrice) * 100),
        expectedProfit,
        role: 'BUY_SLOT',
      });
    }
  }

  // Sort buy levels by price ascending
  levels.sort((a, b) => a.price - b.price);

  // 5. Generate Sell Levels (Up)
  const totalBuyShares = levels
    .filter(l => l.side === 'BUY')
    .reduce((acc, curr) => acc + curr.shares, 0);

  // Sell waterlines consume the swing inventory ledger. Initial swing is
  // available immediately; planned buys can refill the same waterlines later.
  const targetSellVolume = Math.max(0, config.swingShares || 0, totalBuyShares);

  const sharesPerSellGrid =
    nUp > 0 ? Math.floor(targetSellVolume / nUp / lotSize) * lotSize : 0;

  for (let k = 1; k <= nUp; k++) {
    let price = 0;
    let refPrice = 0;

    if (config.gridType === GridType.GEOMETRIC) {
      price = roundToTick(basePrice * Math.pow(1 + rUp, k));
      refPrice = roundToTick(basePrice * Math.pow(1 + rUp, k - 1));
    } else {
      price = roundToTick(basePrice * (1 + rUp * k));
      refPrice = roundToTick(basePrice * (1 + rUp * (k - 1)));
    }

    const shares = sharesPerSellGrid;

    const expectedProfit = Math.max(0, (price - refPrice) * shares);

    if (shares > 0) {
      levels.push({
        id: `sell-${k}`,
        levelIndex: k,
        side: 'SELL',
        price,
        shares,
        amount: shares * price,
        pctFromBase: ((price - basePrice) / basePrice) * 100,
        expectedProfit,
        role: 'SELL_WATERLINE',
      });
    }
  }

  // Final Sort
  levels.sort((a, b) => a.price - b.price);

  // Guards / Stats
  const totalBuyPlanned = levels
    .filter(l => l.side === 'BUY')
    .reduce((acc, curr) => acc + curr.amount, 0);

  if (totalBuyPlanned === 0 && nDown > 0) {
    errors.push(
      '预算太小或限制太严格，无法生成买入网格。请检查资金规模或最小成交额。'
    );
  }

  return {
    isValid: errors.length === 0,
    errors,
    levels,
    basePrice,
    guards: {
      totalInvested: currentPosValue + totalBuyPlanned,
      maxPositionValue: maxPosValue,
      buyBudget: actualBuyBudget,
    },
  };
};
