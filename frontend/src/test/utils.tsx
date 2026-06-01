/* eslint-disable react-refresh/only-export-components */
import { render, type RenderOptions } from '@testing-library/react';
import React, { type ReactElement } from 'react';
import {
  Provider as UrqlProvider,
  Client,
  cacheExchange,
  fetchExchange,
} from 'urql';

import { ThemeProvider } from '@/components/ThemeProvider';
import { TooltipProvider } from '@/components/ui/tooltip';

// Create test-specific URQL client
const createTestUrqlClient = () => {
  return new Client({
    url: 'http://localhost:3000/graphql',
    exchanges: [cacheExchange, fetchExchange],
    suspense: false,
  });
};

interface AllTheProvidersProps {
  children: React.ReactNode;
}

const AllTheProviders = ({ children }: AllTheProvidersProps) => {
  const urqlClient = createTestUrqlClient();

  return (
    <ThemeProvider>
      <UrqlProvider value={urqlClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </UrqlProvider>
    </ThemeProvider>
  );
};

const customRender = (
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'>
) => render(ui, { wrapper: AllTheProviders, ...options });

export * from '@testing-library/react';
export { customRender as render };
