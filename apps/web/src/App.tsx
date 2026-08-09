import { AlertTriangle, LoaderCircle, RefreshCw } from 'lucide-react';
import { useEffect } from 'react';
import { Provider as UrqlProvider } from 'urql';
import { Switch, Route, useLocation } from 'wouter';

import ErrorBoundary from '@/components/ErrorBoundary';
import NotFound from '@/components/NotFound';
import { StudioWorkspace } from '@/components/studio-workspace';
import { ThemeProvider } from '@/components/ThemeProvider';
import { Button } from '@/components/ui/button';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AuthProvider, useAuth } from '@/core/auth';
import { urqlClient } from '@/core/graphql';
import { LoginPage, safeInternalPath } from '@/features/auth';
import {
  TradingSafetyBar,
  TradingSafetyProvider,
} from '@/features/trading-safety';
import { useAutoHideScrollbars } from '@/hooks/useAutoHideScrollbars';
import { useWatchlist } from '@/hooks/useWatchlist';
import { appRoutes, preloadImportantRoutes } from '@/router';
import { tradingAccountConfig } from '@/shared/utils/env';

function WatchlistBootstrap() {
  useWatchlist();
  return null;
}

function renderTradingSafetyStatusBar(currentUserLabel: string) {
  return <TradingSafetyBar currentUserLabel={currentUserLabel} />;
}

function Router({ accountId }: { accountId: string }) {
  useEffect(() => {
    preloadImportantRoutes();
  }, []);

  return (
    <TradingSafetyProvider accountId={accountId}>
      <StudioWorkspace renderStatusBar={renderTradingSafetyStatusBar}>
        <Switch>
          {appRoutes.map(route => (
            <Route
              key={route.path}
              path={route.path}
              component={route.component}
            />
          ))}
          <Route component={NotFound} />
        </Switch>
      </StudioWorkspace>
    </TradingSafetyProvider>
  );
}

function SessionStatusPage({
  title,
  detail,
  actionLabel,
  onAction,
  isLoading = false,
}: {
  title: string;
  detail: string;
  actionLabel?: string;
  onAction?: () => void;
  isLoading?: boolean;
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-[#050914] px-4 text-slate-100">
      <div className="w-full max-w-md rounded-xl border border-white/10 bg-[#0a1020] p-8 text-center shadow-2xl shadow-black/40">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-xl border border-red-500/20 bg-red-500/10 text-red-400">
          {isLoading ? (
            <LoaderCircle className="h-5 w-5 animate-spin" />
          ) : (
            <AlertTriangle className="h-5 w-5" />
          )}
        </div>
        <h1 className="mt-5 text-lg font-semibold text-white">{title}</h1>
        <p className="mt-2 text-sm leading-6 text-slate-400">{detail}</p>
        {actionLabel && onAction && (
          <Button
            type="button"
            onClick={onAction}
            className="mt-6 cursor-pointer bg-red-600 hover:bg-red-500"
          >
            <RefreshCw className="h-4 w-4" />
            {actionLabel}
          </Button>
        )}
      </div>
    </main>
  );
}

function AuthenticatedApp() {
  const {
    bootstrapStatus,
    bootstrapError,
    isAuthenticated,
    user,
    logout,
    retryBootstrap,
  } = useAuth();
  const [location, navigate] = useLocation();
  const currentPath = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  const nextPath =
    location === '/login'
      ? safeInternalPath(
          new URLSearchParams(window.location.search).get('next')
        )
      : safeInternalPath(currentPath);

  useEffect(() => {
    if (bootstrapStatus !== 'ready') return;
    if (!isAuthenticated && location !== '/login') {
      navigate(`/login?next=${encodeURIComponent(nextPath)}`, {
        replace: true,
      });
    } else if (isAuthenticated && location === '/login') {
      navigate(nextPath, { replace: true });
    }
  }, [bootstrapStatus, isAuthenticated, location, navigate, nextPath]);

  if (bootstrapStatus === 'initializing') {
    return (
      <SessionStatusPage
        title="正在恢复安全会话"
        detail="正在验证 HttpOnly 刷新凭证，业务请求会在认证完成后开始。"
        isLoading
      />
    );
  }

  if (bootstrapStatus === 'error') {
    return (
      <SessionStatusPage
        title="暂时无法连接认证服务"
        detail={bootstrapError?.message || '请检查后端服务与网络连接。'}
        actionLabel="重新连接"
        onAction={() => void retryBootstrap()}
      />
    );
  }

  if (!isAuthenticated || !user) {
    return <LoginPage nextPath={nextPath} />;
  }

  const configuredAccountId = tradingAccountConfig.defaultAccountId;
  if (
    configuredAccountId &&
    !user.authorizedAccountIds.includes(configuredAccountId)
  ) {
    return (
      <SessionStatusPage
        title="默认账户未授权"
        detail="VITE_DEFAULT_ACCOUNT_ID 与当前用户的后端账户授权不一致，请修正本地环境配置后重新登录。"
        actionLabel="退出登录"
        onAction={() => void logout()}
      />
    );
  }

  if (location === '/login') {
    return (
      <SessionStatusPage
        title="正在进入工作台"
        detail="安全会话已恢复，正在返回原页面。"
        isLoading
      />
    );
  }
  const accountId = configuredAccountId || user.authorizedAccountIds[0] || '';

  return (
    <UrqlProvider value={urqlClient}>
      <WatchlistBootstrap />
      <Router accountId={accountId} />
    </UrqlProvider>
  );
}

function App() {
  useAutoHideScrollbars();

  return (
    <ErrorBoundary>
      <ThemeProvider>
        <TooltipProvider>
          <AuthProvider>
            <Toaster />
            <AuthenticatedApp />
          </AuthProvider>
        </TooltipProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
