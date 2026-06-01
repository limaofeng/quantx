import { FitAddon } from '@xterm/addon-fit';
import { Terminal } from '@xterm/xterm';
import {
  Terminal as TerminalIcon,
  Download,
  Trash2,
  Copy,
  Pause,
  Play,
  Wifi,
  WifiOff,
} from 'lucide-react';
import { useState, useEffect, useRef } from 'react';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { useToast } from '@/hooks/use-toast';

import '@xterm/xterm/css/xterm.css';

export interface LogEntry {
  id: string;
  timestamp: Date;
  level: 'info' | 'success' | 'warning' | 'error';
  message: string;
}

interface XTerminalLogProps {
  strategyId: string;
  isRunning?: boolean;
}

export default function XTerminalLog({
  strategyId,
  isRunning = false,
}: XTerminalLogProps) {
  const { toast } = useToast();
  const [isConnected, setIsConnected] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const terminalRef = useRef<HTMLDivElement>(null);
  const terminalInstanceRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>();
  const logBufferRef = useRef<string>('');

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
        writeToTerminal(
          '\r\n\x1b[32m[INFO]\x1b[0m WebSocket connected, subscribing to strategy logs...\r\n'
        );

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

          if (data.type === 'subscribed') {
            writeToTerminal(
              `\x1b[32m[INFO]\x1b[0m Successfully subscribed to strategy ${data.strategyId}\r\n`
            );
          } else if (
            data.type === 'log' &&
            data.strategyId === strategyId &&
            !isPaused
          ) {
            const logData = data.data;
            const timestamp = new Date(logData.timestamp).toLocaleTimeString();
            const levelColor = getLevelColor(logData.level);
            const levelText = logData.level.toUpperCase().padEnd(7);

            writeToTerminal(
              `\x1b[90m${timestamp}\x1b[0m ${levelColor}[${levelText}]\x1b[0m ${logData.message}\r\n`
            );
          }
        } catch {
          // WebSocket 消息解析失败,静默处理
        }
      };

      wsRef.current.onclose = () => {
        setIsConnected(false);
        writeToTerminal(
          '\r\n\x1b[31m[ERROR]\x1b[0m WebSocket disconnected, attempting to reconnect...\r\n'
        );
        // Try to reconnect after 3 seconds
        reconnectTimeoutRef.current = setTimeout(connectWebSocket, 3000);
      };

      wsRef.current.onerror = () => {
        setIsConnected(false);
        writeToTerminal(
          '\x1b[31m[ERROR]\x1b[0m WebSocket connection error\r\n'
        );
      };
    } catch {
      setIsConnected(false);
    }
  };

  const getLevelColor = (level: string) => {
    switch (level) {
      case 'success':
        return '\x1b[32m'; // Green
      case 'warning':
        return '\x1b[33m'; // Yellow
      case 'error':
        return '\x1b[31m'; // Red
      default:
        return '\x1b[36m'; // Cyan for info
    }
  };

  const writeToTerminal = (text: string) => {
    if (terminalInstanceRef.current) {
      terminalInstanceRef.current.write(text);
      logBufferRef.current += text;
    }
  };

  const initializeTerminal = () => {
    if (!terminalRef.current) return;

    // Create terminal instance
    const terminal = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: 'Monaco, Menlo, "Ubuntu Mono", monospace',
      theme: {
        background: '#1a1a1a',
        foreground: '#ffffff',
        cursor: '#ffffff',
        selection: '#ffffff40',
        black: '#000000',
        red: '#ff5555',
        green: '#50fa7b',
        yellow: '#f1fa8c',
        blue: '#bd93f9',
        magenta: '#ff79c6',
        cyan: '#8be9fd',
        white: '#bfbfbf',
        brightBlack: '#4d4d4d',
        brightRed: '#ff6e67',
        brightGreen: '#5af78e',
        brightYellow: '#f4f99d',
        brightBlue: '#caa9fa',
        brightMagenta: '#ff92d0',
        brightCyan: '#9aedfe',
        brightWhite: '#e6e6e6',
      },
      cols: 80,
      rows: 24,
    });

    // Create fit addon
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);

    // Open terminal
    terminal.open(terminalRef.current);
    fitAddon.fit();

    // Store references
    terminalInstanceRef.current = terminal;
    fitAddonRef.current = fitAddon;

    // Initial welcome message
    terminal.write(
      '\x1b[1;34m┌─────────────────────────────────────────────────────────────────────────────┐\x1b[0m\r\n'
    );
    terminal.write(
      '\x1b[1;34m│\x1b[0m                            \x1b[1;33mA股量化交易策略日志终端\x1b[0m                           \x1b[1;34m│\x1b[0m\r\n'
    );
    terminal.write(
      '\x1b[1;34m└─────────────────────────────────────────────────────────────────────────────┘\x1b[0m\r\n\r\n'
    );
    terminal.write(`\x1b[36m[INFO]\x1b[0m Strategy ID: ${strategyId}\r\n`);
    terminal.write(
      `\x1b[36m[INFO]\x1b[0m Status: ${isRunning ? '\x1b[32mRunning\x1b[0m' : '\x1b[33mStopped\x1b[0m'}\r\n`
    );
    terminal.write(`\x1b[36m[INFO]\x1b[0m Connecting to log stream...\r\n\r\n`);

    // Add some mock historical logs
    const mockLogs = [
      {
        level: 'info',
        message: '策略引擎初始化完成',
        timestamp: new Date(Date.now() - 10000),
      },
      {
        level: 'success',
        message: '成功连接到行情数据源',
        timestamp: new Date(Date.now() - 9000),
      },
      {
        level: 'info',
        message: '开始监控股票: 000001, 600519, 000002',
        timestamp: new Date(Date.now() - 8000),
      },
      {
        level: 'info',
        message: '技术指标计算: MA5=12.45, MA20=12.18, RSI=65.2',
        timestamp: new Date(Date.now() - 7000),
      },
      {
        level: 'warning',
        message: '股票 600519 价格波动异常: +2.8%',
        timestamp: new Date(Date.now() - 6000),
      },
      {
        level: 'success',
        message: '生成买入意图: 000001 @ ¥12.48',
        timestamp: new Date(Date.now() - 5000),
      },
      {
        level: 'info',
        message: '风险控制检查通过，订单已提交',
        timestamp: new Date(Date.now() - 4000),
      },
      {
        level: 'error',
        message: '股票 000002 数据获取失败，将重试',
        timestamp: new Date(Date.now() - 3000),
      },
      {
        level: 'success',
        message: '策略执行周期完成，等待下次检查...',
        timestamp: new Date(Date.now() - 2000),
      },
    ];

    mockLogs.forEach(log => {
      const timestamp = log.timestamp.toLocaleTimeString();
      const levelColor = getLevelColor(log.level);
      const levelText = log.level.toUpperCase().padEnd(7);
      terminal.write(
        `\x1b[90m${timestamp}\x1b[0m ${levelColor}[${levelText}]\x1b[0m ${log.message}\r\n`
      );
    });

    terminal.write(
      '\r\n\x1b[36m[INFO]\x1b[0m Waiting for real-time logs...\r\n'
    );

    // Handle resize
    const handleResize = () => {
      if (fitAddonRef.current) {
        fitAddonRef.current.fit();
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      terminal.dispose();
    };
  };

  useEffect(() => {
    const cleanup = initializeTerminal();
    connectWebSocket();

    return () => {
      if (cleanup) cleanup();
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyId]);

  const clearTerminal = () => {
    if (terminalInstanceRef.current) {
      terminalInstanceRef.current.clear();
      logBufferRef.current = '';
      writeToTerminal('\x1b[36m[INFO]\x1b[0m Terminal cleared\r\n');
    }
    toast({
      title: '终端已清空',
      description: '所有日志已清除',
    });
  };

  const exportLogs = () => {
    // eslint-disable-next-line no-control-regex
    const ansiRegex = /\x1b\[[0-9;]*m/g; // Remove ANSI codes
    const logText = logBufferRef.current
      .replace(ansiRegex, '')
      .replace(/\r\n/g, '\n');

    const blob = new Blob([logText], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `strategy-${strategyId}-terminal-${new Date().toISOString().split('T')[0]}.log`;
    a.click();
    URL.revokeObjectURL(url);

    toast({
      title: '日志已导出',
      description: '终端日志文件已下载到本地',
    });
  };

  const copyLogs = () => {
    // eslint-disable-next-line no-control-regex
    const ansiRegex = /\x1b\[[0-9;]*m/g; // Remove ANSI codes
    const logText = logBufferRef.current
      .replace(ansiRegex, '')
      .replace(/\r\n/g, '\n');

    navigator.clipboard.writeText(logText).then(() => {
      toast({
        title: '已复制到剪贴板',
        description: '终端日志已复制到剪贴板',
      });
    });
  };

  const togglePause = () => {
    setIsPaused(!isPaused);
    const status = !isPaused ? '已暂停' : '已恢复';
    writeToTerminal(`\r\n\x1b[33m[INFO]\x1b[0m 日志流${status}\r\n`);
    toast({
      title: `日志流${status}`,
      description: `实时日志更新${status}`,
    });
  };

  return (
    <Card className="h-full">
      <div className="p-4 border-b bg-slate-900 text-white">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TerminalIcon className="h-5 w-5" />
            <h3 className="font-mono font-semibold">
              Terminal - Strategy {strategyId}
            </h3>
            <Badge
              variant={isRunning ? 'default' : 'secondary'}
              className="ml-2"
            >
              {isRunning ? 'Running' : 'Stopped'}
            </Badge>
            <div className="flex items-center gap-1 ml-2">
              {isConnected ? (
                <Wifi className="h-4 w-4 text-green-400" />
              ) : (
                <WifiOff className="h-4 w-4 text-red-400" />
              )}
              <span className="text-xs">
                {isConnected ? 'Connected' : 'Disconnected'}
              </span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={togglePause}
              className="text-white border-gray-600 hover:bg-gray-800"
            >
              {isPaused ? (
                <Play className="h-4 w-4" />
              ) : (
                <Pause className="h-4 w-4" />
              )}
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={copyLogs}
              className="text-white border-gray-600 hover:bg-gray-800"
            >
              <Copy className="h-4 w-4" />
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={exportLogs}
              className="text-white border-gray-600 hover:bg-gray-800"
            >
              <Download className="h-4 w-4" />
            </Button>

            <Button
              variant="outline"
              size="sm"
              onClick={clearTerminal}
              className="text-white border-gray-600 hover:bg-gray-800"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      <div className="p-4 bg-[#1a1a1a]">
        <div
          ref={terminalRef}
          className="w-full h-96 rounded border border-gray-700"
          style={{ minHeight: '400px' }}
        />
      </div>
    </Card>
  );
}
