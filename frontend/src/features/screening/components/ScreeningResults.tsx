import {
  ArrowDownIcon,
  ArrowUpIcon,
  Download,
  LayoutList,
  MoreHorizontal,
  Zap,
  Search,
  AlertTriangle,
} from 'lucide-react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useToast } from '@/hooks/use-toast';

import { type StockScreeningMeta, type StockScreeningResult } from '../types';

interface ScreeningResultsProps {
  screeningLoading: boolean;
  results?: StockScreeningResult[];
  meta?: StockScreeningMeta;
  error?: string;
}

export function ScreeningResults({
  screeningLoading,
  results,
  meta,
  error,
}: ScreeningResultsProps) {
  const { toast } = useToast();

  const handleExport = () => {
    toast({
      title: '正在生成导出文件...',
      description: '结果将以 CSV 格式下载',
    });
  };

  const handleAction = (action: string, code: string, name: string) => {
    toast({
      title: '操作已记录',
      description: `已请求 ${action}: ${name} (${code})`,
    });
  };

  const displayData =
    results && results.length > 0 ? results : screeningLoading ? [] : [];

  const formatPercent = (val: number, bold = false) => {
    const isPositive = val > 0;
    const isNegative = val < 0;
    // China Market: Red = Up, Green = Down
    const colorClass = isPositive
      ? 'text-red-400'
      : isNegative
        ? 'text-emerald-400'
        : 'text-slate-500';

    return (
      <span
        className={`flex items-center justify-end font-mono ${colorClass} ${bold ? 'font-bold' : ''}`}
      >
        {isPositive ? (
          <ArrowUpIcon className="h-3 w-3 mr-0.5" />
        ) : isNegative ? (
          <ArrowDownIcon className="h-3 w-3 mr-0.5" />
        ) : null}
        {Math.abs(val).toFixed(2)}%
      </span>
    );
  };

  const formatPrice = (val: number) => `¥${val.toFixed(2)}`;

  // Value visualization helper map
  // 0-100 normalization color
  const getKDJColor = (val: number) => {
    if (val < 20) return 'text-purple-400 font-bold'; // Oversold
    if (val > 80) return 'text-rose-400 font-bold'; // Overbought
    return 'text-slate-500';
  };

  const getRSIColor = (val: number) => {
    if (val < 30) return 'text-emerald-400 font-bold';
    if (val > 70) return 'text-rose-400 font-bold';
    return 'text-slate-500';
  };

  // Signal badge coloring: oversold/rebound → emerald, momentum/breakout → rose, crossover → amber
  const getSignalBadgeClass = (signal: string) => {
    const oversold = ['超跌反弹', '布林下轨反弹', 'RSI 超卖'];
    const momentum = ['强势股', '布林上轨突破', 'RSI 强势', '放量突破'];
    const crossover = ['KDJ 金叉', '均线金叉'];
    if (oversold.includes(signal))
      return 'border-emerald-500/30 text-emerald-400 bg-emerald-500/10';
    if (momentum.includes(signal))
      return 'border-rose-500/30 text-rose-400 bg-rose-500/10';
    if (crossover.includes(signal))
      return 'border-amber-500/30 text-amber-400 bg-amber-500/10';
    return 'border-purple-500/30 text-purple-400 bg-purple-500/10';
  };

  return (
    <div className="h-full flex flex-col bg-transparent">
      {/* Table Toolbar - Matches Terminal Header style */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-white/5 bg-slate-900/40">
        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-2">
            <LayoutList className="h-4 w-4 text-slate-400" />
            <Badge
              variant="outline"
              className="font-mono font-normal text-[10px] border-slate-700 text-slate-400"
            >
              总数: {meta?.total ?? displayData.length}
            </Badge>
            {meta?.signalVersion && (
              <Badge
                variant="outline"
                className="font-mono font-normal text-[10px] border-slate-700 text-slate-400"
              >
                版本: {meta.signalVersion}
              </Badge>
            )}
            {meta?.calculatedAt && (
              <Badge
                variant="outline"
                className="font-mono font-normal text-[10px] border-slate-700 text-slate-400"
              >
                最后计算: {new Date(meta.calculatedAt).toLocaleString()}
              </Badge>
            )}
            {meta?.hasStaleData && (
              <Badge
                variant="outline"
                className="font-mono font-normal text-[10px] border-amber-500/30 text-amber-300 bg-amber-500/10"
              >
                <AlertTriangle className="mr-1 h-3 w-3" />
                非今日快照
              </Badge>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleExport}
            className="h-7 text-xs gap-1 text-slate-400 hover:text-white hover:bg-white/5"
          >
            <Download className="h-3.5 w-3.5" />
            导出
          </Button>
        </div>
      </div>

      {/* Main Table Area */}
      <div className="flex-1 overflow-hidden relative">
        {(error || meta?.warnings?.length) && (
          <div className="px-4 py-2 border-b border-amber-500/20 bg-amber-500/10 text-[11px] text-amber-200 flex items-center gap-2">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span className="truncate">
              {error || meta?.warnings?.join('；')}
            </span>
          </div>
        )}
        {screeningLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950/60 backdrop-blur-[2px] z-20">
            <div className="flex flex-col items-center">
              <div className="h-8 w-8 border-2 border-slate-700 border-t-purple-500 rounded-full animate-spin mb-2"></div>
              <p className="text-xs font-mono text-slate-400 flex items-center gap-2">
                <Zap className="h-3 w-3" /> 正在执行筛选...
              </p>
            </div>
          </div>
        )}

        <ScrollArea className="h-full w-full">
          <Table>
            <TableHeader className="sticky top-0 z-10 bg-[#0F1729]">
              <TableRow className="border-b border-white/5 shadow-sm hover:bg-transparent">
                <TableHead className="w-[220px] pl-4 text-slate-500 text-[10px] uppercase font-bold tracking-wider">
                  代码 / 名称
                </TableHead>
                <TableHead className="text-right w-[100px] text-slate-500 text-[10px] uppercase font-bold tracking-wider">
                  价格
                </TableHead>
                <TableHead className="text-right w-[100px] text-slate-500 text-[10px] uppercase font-bold tracking-wider">
                  涨跌幅
                </TableHead>
                <TableHead className="text-center w-[140px] text-slate-500 text-[10px] uppercase font-bold tracking-wider">
                  信号
                </TableHead>

                {/* Technicals Group */}
                <TableHead className="text-center min-w-[140px] border-l border-white/5 bg-white/[0.02] text-slate-500 text-[10px] uppercase font-bold tracking-wider">
                  KDJ (9,3,3)
                </TableHead>
                <TableHead className="text-center min-w-[140px] bg-white/[0.02] text-slate-500 text-[10px] uppercase font-bold tracking-wider">
                  RSI (6/12/24)
                </TableHead>
                <TableHead className="text-center min-w-[80px] bg-white/[0.02] border-r border-white/5 text-slate-500 text-[10px] uppercase font-bold tracking-wider">
                  量比
                </TableHead>

                {/* Analysis Group */}
                <TableHead className="text-right min-w-[100px] text-slate-500 text-[10px] uppercase font-bold tracking-wider">
                  距高回撤
                </TableHead>
                <TableHead className="text-right min-w-[80px] text-slate-500 text-[10px] uppercase font-bold tracking-wider">
                  ROE
                </TableHead>
                <TableHead className="w-[50px]"></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody className="text-xs font-mono relative min-h-[400px]">
              {displayData.length === 0 && !screeningLoading && (
                <TableRow>
                  <TableCell colSpan={13} className="h-[400px] text-center">
                    <div className="flex flex-col items-center justify-center text-slate-500 space-y-3">
                      <div className="w-12 h-12 rounded-full bg-slate-800/50 flex items-center justify-center">
                        <Search className="h-6 w-6 text-slate-400" />
                      </div>
                      <div className="space-y-1">
                        <p className="font-bold text-slate-300">
                          未找到符合条件的股票
                        </p>
                        <p className="text-xs">
                          请尝试放宽筛选条件，或减少选定的信号策略
                        </p>
                      </div>
                    </div>
                  </TableCell>
                </TableRow>
              )}
              {displayData.map(stock => (
                <TableRow
                  key={stock.code}
                  className="group hover:bg-white/[0.02] transition-colors border-b-white/5"
                >
                  <TableCell className="pl-4 py-3">
                    <div className="flex flex-col gap-0.5 font-sans">
                      <span className="font-bold text-sm text-slate-200">
                        {stock.name}
                      </span>
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[10px] text-slate-500">
                          {stock.code}
                        </span>
                        <span className="inline-flex items-center whitespace-nowrap text-[9px] text-slate-600 px-1.5 py-0.5 border border-slate-800 rounded-sm">
                          {stock.industry}
                        </span>
                      </div>
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-medium text-slate-300">
                    {formatPrice(stock.currentPrice)}
                  </TableCell>
                  <TableCell className="text-right">
                    {formatPercent(stock.changePct, true)}
                  </TableCell>
                  <TableCell className="text-center">
                    <div className="flex flex-wrap gap-1 justify-center">
                      {stock.matchedStrategies.map((s, idx) => (
                        <Badge
                          key={idx}
                          variant="outline"
                          className={`text-[9px] h-5 px-1.5 font-normal whitespace-nowrap ${getSignalBadgeClass(s)}`}
                        >
                          {s}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>

                  {/* Indicators Visuals */}
                  <TableCell
                    className={`border-l border-white/5 ${stock.matchedStrategies.includes('KDJ 金叉') ? 'bg-amber-500/5 ring-inset ring-1 ring-amber-500/20' : 'bg-white/[0.02]'}`}
                  >
                    <div className="flex items-center justify-center gap-3 text-[11px]">
                      <div className="flex flex-col items-center gap-0.5">
                        <span
                          className={`leading-none ${getKDJColor(stock.k)}`}
                        >
                          {stock.k.toFixed(0)}
                        </span>
                      </div>
                      <div className="flex flex-col items-center gap-0.5">
                        <span className="leading-none text-slate-500">
                          {stock.d.toFixed(0)}
                        </span>
                      </div>
                      <div className="flex flex-col items-center gap-0.5">
                        <span
                          className={`leading-none ${getKDJColor(stock.j)}`}
                        >
                          {stock.j.toFixed(0)}
                        </span>
                      </div>
                    </div>
                  </TableCell>

                  <TableCell
                    className={
                      stock.matchedStrategies.some(
                        s => s === 'RSI 超卖' || s === 'RSI 强势'
                      )
                        ? 'bg-rose-500/5 ring-inset ring-1 ring-rose-500/20'
                        : 'bg-white/[0.02]'
                    }
                  >
                    <div className="flex items-center justify-center gap-2 text-[11px]">
                      <span className={getRSIColor(stock.rsi6)}>
                        {stock.rsi6.toFixed(0)}
                      </span>
                      <span className="text-slate-700">/</span>
                      <span className={getRSIColor(stock.rsi12)}>
                        {stock.rsi12.toFixed(0)}
                      </span>
                      <span className="text-slate-700">/</span>
                      <span className="text-slate-600">
                        {stock.rsi24.toFixed(0)}
                      </span>
                    </div>
                  </TableCell>

                  <TableCell
                    className={`text-center border-r border-white/5 ${stock.matchedStrategies.includes('放量突破') ? 'bg-amber-500/5 ring-inset ring-1 ring-amber-500/20' : 'bg-white/[0.02]'}`}
                  >
                    <div
                      className={`font-medium ${stock.volumeRatio > 1.5 ? 'text-amber-500' : 'text-slate-500'}`}
                    >
                      {stock.volumeRatio.toFixed(1)}
                    </div>
                  </TableCell>

                  <TableCell className="text-right">
                    <div
                      className={`font-medium ${stock.priceDropPct < -20 ? 'text-emerald-400' : 'text-slate-300'}`}
                    >
                      {stock.priceDropPct.toFixed(1)}%
                    </div>
                    <div className="text-[10px] text-slate-600">
                      {stock.daysSincePeak}天前
                    </div>
                  </TableCell>

                  <TableCell className="text-right text-slate-500">
                    {stock.roe?.toFixed(1)}%
                  </TableCell>

                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6 text-slate-600 hover:text-white"
                        >
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent
                        align="end"
                        className="bg-slate-900 border-white/10 text-slate-300"
                      >
                        <DropdownMenuItem
                          className="focus:bg-white/10 focus:text-white cursor-pointer"
                          onClick={() =>
                            handleAction('加入自选', stock.code, stock.name)
                          }
                        >
                          加入自选
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          className="focus:bg-white/10 focus:text-white cursor-pointer"
                          onClick={() =>
                            handleAction('趋势分析', stock.code, stock.name)
                          }
                        >
                          趋势分析
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <ScrollBar orientation="horizontal" className="bg-slate-900" />
        </ScrollArea>
      </div>
    </div>
  );
}
