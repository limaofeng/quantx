// 生成模拟股票数据的工具函数

export interface CandlestickData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export interface LineData {
  time: number;
  value: number;
  volume?: number;
}

// 生成分时数据（今日）
export function generateIntradayData(basePrice: number): LineData[] {
  const data: LineData[] = [];
  const now = new Date();
  const today = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    9,
    30
  ); // 9:30开盘
  const endTime = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    15,
    0
  ); // 15:00收盘

  let currentPrice = basePrice;
  let currentTime = today.getTime();

  // 生成分钟级别数据
  while (currentTime <= endTime.getTime()) {
    const time = new Date(currentTime);

    // 跳过中午休市时间 (11:30-13:00)
    if (time.getHours() === 11 && time.getMinutes() >= 30) {
      currentTime += 90 * 60 * 1000; // 跳过90分钟
      continue;
    }
    if (time.getHours() === 12) {
      currentTime += 60 * 1000;
      continue;
    }

    // 模拟价格波动
    const change = (Math.random() - 0.5) * basePrice * 0.002; // ±0.2%的随机波动
    currentPrice = Math.max(currentPrice + change, basePrice * 0.85); // 限制跌幅
    currentPrice = Math.min(currentPrice, basePrice * 1.15); // 限制涨幅

    data.push({
      time: Math.floor(currentTime / 1000),
      value: Number(currentPrice.toFixed(2)),
      volume: Math.floor(Math.random() * 50000) + 10000,
    });

    currentTime += 60 * 1000; // 每分钟一个数据点
  }

  return data;
}

// 生成K线数据（历史数据）
export function generateCandlestickData(
  basePrice: number,
  days: number = 30
): CandlestickData[] {
  const data: CandlestickData[] = [];
  const endDate = new Date();

  let currentPrice = basePrice * 0.9; // 从较低价格开始，模拟上涨趋势

  // 从最早的日期开始生成
  for (let i = days - 1; i >= 0; i--) {
    const date = new Date(endDate);
    date.setDate(endDate.getDate() - i);

    // 跳过周末
    if (date.getDay() === 0 || date.getDay() === 6) {
      continue;
    }

    const open = currentPrice;

    // 模拟日内波动
    const dailyChange = (Math.random() - 0.45) * basePrice * 0.03; // 轻微上涨趋势
    const volatility = basePrice * 0.02; // 2%的日内波动

    const high = open + Math.random() * volatility + Math.max(0, dailyChange);
    const low = open - Math.random() * volatility + Math.min(0, dailyChange);
    const close = open + dailyChange + (Math.random() - 0.5) * volatility * 0.5;

    data.push({
      time: Math.floor(date.getTime() / 1000),
      open: Number(open.toFixed(2)),
      high: Number(Math.max(open, high, close).toFixed(2)),
      low: Number(Math.min(open, low, close).toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: Math.floor(Math.random() * 1000000) + 500000,
    });

    currentPrice = close;
  }

  // 确保数据按时间升序排列
  return data.sort((a, b) => a.time - b.time);
}

// 生成不同时间周期的数据
export function generateHistoricalData(basePrice: number, period: string) {
  switch (period) {
    // 分时数据（线图数据）
    case 'intraday':
      return generateIntradayData(basePrice).map(d => ({
        time: d.time,
        open: d.value,
        high: d.value * 1.002,
        low: d.value * 0.998,
        close: d.value,
        volume: d.volume,
      }));

    // 五日分时数据（线图数据）
    case '5D':
      return generateMultiDayIntradayData(basePrice, 5);

    // K线数据
    case '1min':
      return generateIntradayKLineData(basePrice, 1); // 1分钟K线
    case '5min':
      return generateIntradayKLineData(basePrice, 5); // 5分钟K线
    case '10min':
      return generateIntradayKLineData(basePrice, 10); // 10分钟K线
    case '30min':
      return generateIntradayKLineData(basePrice, 30); // 30分钟K线
    case '1hour':
      return generateIntradayKLineData(basePrice, 60); // 1小时K线
    case 'daily':
      return generateCandlestickData(basePrice, 30); // 日K线
    case 'weekly':
      return generateWeeklyKLineData(basePrice, 20); // 周K线
    case 'monthly':
      return generateMonthlyKLineData(basePrice, 12); // 月K线
    default:
      return generateCandlestickData(basePrice, 30);
  }
}

// 生成多日分时数据
export function generateMultiDayIntradayData(
  basePrice: number,
  days: number
): CandlestickData[] {
  const data: CandlestickData[] = [];
  const endDate = new Date();

  // 从最早的日期开始生成
  for (let day = days - 1; day >= 0; day--) {
    const currentDate = new Date(endDate);
    currentDate.setDate(endDate.getDate() - day);

    // 跳过周末
    if (currentDate.getDay() === 0 || currentDate.getDay() === 6) continue;

    // 为每一天生成简化的几个数据点（避免太多数据点）
    const dayStart = new Date(
      currentDate.getFullYear(),
      currentDate.getMonth(),
      currentDate.getDate(),
      9,
      30
    );
    const dayEnd = new Date(
      currentDate.getFullYear(),
      currentDate.getMonth(),
      currentDate.getDate(),
      15,
      0
    );

    let currentPrice = basePrice * (0.95 + Math.random() * 0.1);
    const intervals = 24; // 每天24个数据点
    const intervalDuration =
      (dayEnd.getTime() - dayStart.getTime()) / intervals;

    for (let i = 0; i < intervals; i++) {
      const pointTime = dayStart.getTime() + i * intervalDuration;
      const timeDate = new Date(pointTime);

      // 跳过中午休市时间
      if (timeDate.getHours() === 11 && timeDate.getMinutes() >= 30) continue;
      if (timeDate.getHours() === 12) continue;

      const change = (Math.random() - 0.5) * basePrice * 0.01;
      currentPrice = Math.max(currentPrice + change, basePrice * 0.9);

      data.push({
        time: Math.floor(pointTime / 1000),
        open: currentPrice,
        high: currentPrice * 1.001,
        low: currentPrice * 0.999,
        close: currentPrice,
        volume: Math.floor(Math.random() * 50000) + 10000,
      });
    }
  }

  // 确保数据按时间升序排列
  return data.sort((a, b) => a.time - b.time);
}

// 生成日内分钟级K线数据
export function generateIntradayKLineData(
  basePrice: number,
  intervalMinutes: number
): CandlestickData[] {
  const data: CandlestickData[] = [];
  const now = new Date();
  const today = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    9,
    30
  );
  const endTime = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
    15,
    0
  );

  let currentPrice = basePrice;
  let currentTime = today.getTime();

  while (currentTime <= endTime.getTime()) {
    const time = new Date(currentTime);

    // 跳过中午休市时间
    if (time.getHours() === 11 && time.getMinutes() >= 30) {
      currentTime += 90 * 60 * 1000;
      continue;
    }
    if (time.getHours() === 12) {
      currentTime += intervalMinutes * 60 * 1000;
      continue;
    }

    const open = currentPrice;
    const change = (Math.random() - 0.5) * basePrice * 0.01;
    const volatility = basePrice * 0.005;

    const high = open + Math.random() * volatility + Math.max(0, change);
    const low = open - Math.random() * volatility + Math.min(0, change);
    const close = open + change + (Math.random() - 0.5) * volatility * 0.3;

    data.push({
      time: Math.floor(currentTime / 1000),
      open: Number(open.toFixed(2)),
      high: Number(Math.max(open, high, close).toFixed(2)),
      low: Number(Math.min(open, low, close).toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: Math.floor(Math.random() * 100000) + 20000,
    });

    currentPrice = close;
    currentTime += intervalMinutes * 60 * 1000;
  }

  // 确保数据按时间升序排列
  return data.sort((a, b) => a.time - b.time);
}

// 生成周K线数据
export function generateWeeklyKLineData(
  basePrice: number,
  weeks: number
): CandlestickData[] {
  const data: CandlestickData[] = [];
  const endDate = new Date();

  let currentPrice = basePrice * 0.9;

  // 从最早的周开始生成
  for (let i = weeks - 1; i >= 0; i--) {
    const weekStart = new Date(endDate);
    weekStart.setDate(endDate.getDate() - i * 7);

    // 设置为周一
    const dayOfWeek = weekStart.getDay();
    const daysToMonday = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
    weekStart.setDate(weekStart.getDate() - daysToMonday);

    const open = currentPrice;
    const weeklyChange = (Math.random() - 0.4) * basePrice * 0.08;
    const volatility = basePrice * 0.06;

    const high = open + Math.random() * volatility + Math.max(0, weeklyChange);
    const low = open - Math.random() * volatility + Math.min(0, weeklyChange);
    const close = open + weeklyChange;

    data.push({
      time: Math.floor(weekStart.getTime() / 1000),
      open: Number(open.toFixed(2)),
      high: Number(Math.max(open, high, close).toFixed(2)),
      low: Number(Math.min(open, low, close).toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: Math.floor(Math.random() * 5000000) + 2000000,
    });

    currentPrice = close;
  }

  // 确保数据按时间升序排列
  return data.sort((a, b) => a.time - b.time);
}

// 生成月K线数据
export function generateMonthlyKLineData(
  basePrice: number,
  months: number
): CandlestickData[] {
  const data: CandlestickData[] = [];
  const endDate = new Date();

  let currentPrice = basePrice * 0.85;

  // 从最早的月份开始生成
  for (let i = months - 1; i >= 0; i--) {
    const monthStart = new Date(
      endDate.getFullYear(),
      endDate.getMonth() - i,
      1
    );

    const open = currentPrice;
    const monthlyChange = (Math.random() - 0.35) * basePrice * 0.15;
    const volatility = basePrice * 0.12;

    const high = open + Math.random() * volatility + Math.max(0, monthlyChange);
    const low = open - Math.random() * volatility + Math.min(0, monthlyChange);
    const close = open + monthlyChange;

    data.push({
      time: Math.floor(monthStart.getTime() / 1000),
      open: Number(open.toFixed(2)),
      high: Number(Math.max(open, high, close).toFixed(2)),
      low: Number(Math.min(open, low, close).toFixed(2)),
      close: Number(close.toFixed(2)),
      volume: Math.floor(Math.random() * 20000000) + 10000000,
    });

    currentPrice = close;
  }

  // 确保数据按时间升序排列
  return data.sort((a, b) => a.time - b.time);
}

// 计算技术指标
export function calculateMA(
  data: CandlestickData[],
  period: number
): LineData[] {
  const ma: LineData[] = [];

  for (let i = period - 1; i < data.length; i++) {
    const sum = data
      .slice(i - period + 1, i + 1)
      .reduce((acc, d) => acc + d.close, 0);
    const average = sum / period;

    ma.push({
      time: data[i].time,
      value: Number(average.toFixed(2)),
    });
  }

  return ma;
}

// 格式化成交量显示
export function formatVolume(volume: number): string {
  if (volume >= 100000000) {
    return `${(volume / 100000000).toFixed(1)}亿`;
  } else if (volume >= 10000) {
    return `${(volume / 10000).toFixed(1)}万`;
  } else {
    return volume.toString();
  }
}
