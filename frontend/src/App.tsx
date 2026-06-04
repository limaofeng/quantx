import { useEffect } from 'react';
import { Provider as UrqlProvider } from 'urql';
import { Switch, Route } from 'wouter';

import ErrorBoundary from '@/components/ErrorBoundary';
import Layout from '@/components/Layout';
import NotFound from '@/components/NotFound';
import { ThemeProvider } from '@/components/ThemeProvider';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { urqlClient } from '@/core/graphql';
import { useAutoHideScrollbars } from '@/hooks/useAutoHideScrollbars';
import { useWatchlist } from '@/hooks/useWatchlist';
import { appRoutes, preloadImportantRoutes } from '@/router';

function WatchlistBootstrap() {
  useWatchlist();
  return null;
}

function Router() {
  useEffect(() => {
    preloadImportantRoutes();
  }, []);

  return (
    <Layout>
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
    </Layout>
  );
}

function App() {
  useAutoHideScrollbars();

  return (
    <ErrorBoundary>
      <ThemeProvider>
        <UrqlProvider value={urqlClient}>
          <TooltipProvider>
            <WatchlistBootstrap />
            <Toaster />
            <Router />
          </TooltipProvider>
        </UrqlProvider>
      </ThemeProvider>
    </ErrorBoundary>
  );
}

export default App;
