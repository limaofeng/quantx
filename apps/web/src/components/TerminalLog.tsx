import {
  Terminal,
  Trash2,
  Download,
  Copy,
  CheckCircle2,
  AlertTriangle,
  Info,
  XCircle,
} from 'lucide-react';
import { useState, useEffect, useRef } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { useToast } from '@/hooks/use-toast';

export interface LogEntry {
  id: string;
  timestamp: Date;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
  data?: unknown;
}

interface TerminalLogProps {
  strategyId: string;
  isRunning?: boolean;
}

export default function TerminalLog({
  strategyId,
  isRunning = false,
}: TerminalLogProps) {
  const { toast } = useToast();
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [isAutoScroll, setIsAutoScroll] = useState(true);
  const [isConnected, setIsConnected] = useState(false);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>();

  // 模拟日志数据（稍后会替换为真实WebSocket数据）
  const mockLogs: LogEntry[] = [
    {
      id: '1',
      timestamp: new Date(Date.now() - 10000),
      level: 'info',
      message: '策略启动，开始初始化...',
    },
    {
      id: '2',
      timestamp: new Date(Date.now() - 9000),
      level: 'info',
      message: '加载股票代码: 000001, 600519, 000002',
    },
    {
      id: '3',
      timestamp: new Date(Date.now() - 8000),
      level: 'success',
      message: '成功连接到数据源，开始监控价格变化',
    },
    {
      id: '4',
      timestamp: new Date(Date.now() - 7000),
      level: 'info',
      message: '计算技术指标: MA5=12.45, MA20=12.18, RSI=65.2',
    },
    {
      id: '5',
      timestamp: new Date(Date.now() - 6000),
      level: 'warning',
      message: '股票 600519 价格波动异常，当前价格: ¥1682.50 (+2.8%)',
    },
    {
      id: '6',
      timestamp: new Date(Date.now() - 5000),
      level: 'success',
      message: '生成买入意图: 000001 @ ¥12.48 (原因: 短期均线上穿长期均线)',
    },
    {
      id: '7',
      timestamp: new Date(Date.now() - 4000),
      level: 'info',
      message: '风险控制检查通过，订单提交成功',
    },
    {
      id: '8',
      timestamp: new Date(Date.now() - 3000),
      level: 'error',
      message: '股票 000002 数据获取失败，将在30秒后重试',
    },
    {
      id: '9',
      timestamp: new Date(Date.now() - 2000),
      level: 'info',
      message: '心跳检测正常，策略运行状态良好',
    },
    {
      id: '10',
      timestamp: new Date(Date.now() - 1000),
      level: 'success',
      message: '策略执行周期完成，等待下次检查...',
    },
  ];

  const connectWebSocket = () => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      return;
    }

    try {
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${window.location.host}/ws`;

      wsRef.current = new WebSocket(wsUrl);

      wsRef.current.onopen = () => {
        setIsConnected(true);
        // Subscribe to strategy logs
        wsRef.current?.send(
          JSON.stringify({
            type: 'subscribe',
            strategyId,
          })
        );
      };

      wsRef.current.onmessage = event => {
        try {
          const data = JSON.parse(event.data);

          if (data.type === 'log' && data.strategyId === strategyId) {
            const newLog: LogEntry = {
              id: data.data.id,
              timestamp: new Date(data.data.timestamp),
              level: data.data.level,
              message: data.data.message,
            };

            setLogs(prev => [...prev, newLog]);
          }
        } catch {
          // WebSocket 消息解析失败,静默处理
        }
      };

      wsRef.current.onclose = () => {
        setIsConnected(false);
        // Try to reconnect after 3 seconds
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 3000);
      };

      wsRef.current.onerror = () => {
        setIsConnected(false);
      };
    } catch {
      setIsConnected(false);
    }
  };

  useEffect(() => {
    // 初始化时加载模拟日志
    setLogs(mockLogs);

    // 连接WebSocket进行实时日志更新
    connectWebSocket();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyId]);

  // 自动滚动到底部
  useEffect(() => {
    if (isAutoScroll && scrollAreaRef.current) {
      const scrollArea = scrollAreaRef.current.querySelector(
        '[data-radix-scroll-area-viewport]'
      );
      if (scrollArea) {
        scrollArea.scrollTop = scrollArea.scrollHeight;
      }
    }
  }, [logs, isAutoScroll]);

  const getLogIcon = (level: LogEntry['level']) => {
    switch (level) {
      case 'success':
        return <CheckCircle2 className="h-4 w-4 text-success" />;
      case 'warning':
        return <AlertTriangle className="h-4 w-4 text-warning" />;
      case 'error':
        return <XCircle className="h-4 w-4 text-destructive" />;
      default:
        return <Info className="h-4 w-4 text-muted-foreground" />;
    }
  };

  const getLogTextColor = (level: LogEntry['level']) => {
    switch (level) {
      case 'success':
        return 'text-success';
      case 'warning':
        return 'text-warning';
      case 'error':
        return 'text-destructive';
      default:
        return 'text-foreground';
    }
  };

  const clearLogs = () => {
    setLogs([]);
    toast({
      title: '日志已清空',
      description: '所有日志记录已清除',
    });
  };

  const exportLogs = () => {
    const logText = logs
      .map(
        log =>
          `[${log.timestamp.toLocaleString()}] [${log.level.toUpperCase()}] ${log.message}`
      )
      .join('\n');

    const blob = new Blob([logText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `strategy-${strategyId}-logs-${new Date().toISOString().split('T')[0]}.txt`;
    a.click();
    URL.revokeObjectURL(url);

    toast({
      title: '日志已导出',
      description: '日志文件已下载到本地',
    });
  };

  const copyAllLogs = () => {
    const logText = logs
      .map(
        log =>
          `[${log.timestamp.toLocaleString()}] [${log.level.toUpperCase()}] ${log.message}`
      )
      .join('\n');

    navigator.clipboard.writeText(logText).then(() => {
      toast({
        title: '已复制到剪贴板',
        description: '所有日志记录已复制到剪贴板',
      });
    });
  };

  return (
    <Card className="h-full">
      <div className="p-4 border-b">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Terminal className="h-5 w-5" />
            <h3 className="font-semibold">运行日志</h3>
            <Badge
              variant={isRunning ? 'default' : 'secondary'}
              className="ml-2"
            >
              {isRunning ? '实时' : '历史'}
            </Badge>
            {isConnected && (
              <Badge variant="outline" className="text-success border-success">
                已连接
              </Badge>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsAutoScroll(!isAutoScroll)}
              className={isAutoScroll ? 'bg-primary/10' : ''}
            >
              {isAutoScroll ? '自动滚动' : '手动滚动'}
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={copyAllLogs}
              disabled={logs.length === 0}
            >
              <Copy className="h-4 w-4" />
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={exportLogs}
              disabled={logs.length === 0}
            >
              <Download className="h-4 w-4" />
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={clearLogs}
              disabled={logs.length === 0}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      <div className="p-4">
        <ScrollArea ref={scrollAreaRef} className="h-96">
          <div className="space-y-2 font-mono text-sm">
            {logs.length === 0 ? (
              <div className="flex items-center justify-center h-32 text-muted-foreground">
                <div className="text-center">
                  <Terminal className="mx-auto h-8 w-8 mb-2" />
                  <p>暂无日志记录</p>
                </div>
              </div>
            ) : (
              logs.map(log => (
                <div
                  key={log.id}
                  className="flex items-start gap-3 p-2 hover:bg-muted/30 rounded group"
                >
                  <div className="flex items-center gap-2 min-w-0 flex-shrink-0">
                    {getLogIcon(log.level)}
                    <span className="text-xs text-muted-foreground whitespace-nowrap">
                      {log.timestamp.toLocaleTimeString()}
                    </span>
                  </div>
                  <div
                    className={`${getLogTextColor(log.level)} flex-1 break-words`}
                  >
                    {log.message}
                  </div>
                </div>
              ))
            )}
          </div>
        </ScrollArea>
      </div>

      {/* 底部状态栏 */}
      <div className="px-4 py-2 border-t bg-muted/20 flex items-center justify-between text-xs text-muted-foreground">
        <div className="flex items-center gap-4">
          <span>总计: {logs.length} 条</span>
          <span>成功: {logs.filter(l => l.level === 'success').length}</span>
          <span>警告: {logs.filter(l => l.level === 'warning').length}</span>
          <span>错误: {logs.filter(l => l.level === 'error').length}</span>
        </div>
        <div>{isRunning ? '实时监控中...' : '策略已停止'}</div>
      </div>
    </Card>
  );
}
