import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { PropsWithChildren, ReactElement } from 'react';
import { MemoryRouter } from 'react-router-dom';
import {
  ApiServiceContext,
  AppConfigContext,
  type AppConfigContextValue,
} from '../app/contexts';
import { createFqmApiService } from '../services/createFqmApiService';
import type { FqmApiService } from '../services/fqmApiService';

interface RenderOptions {
  route?: string;
  service?: FqmApiService;
  config?: Partial<AppConfigContextValue>;
}

const defaultConfig: AppConfigContextValue = {
  apiBaseUrl: 'http://localhost:8000',
  mode: 'real',
  version: 'test',
  setApiBaseUrl: () => undefined,
  resetApiBaseUrl: () => undefined,
};

export function renderWithProviders(
  ui: ReactElement,
  options: RenderOptions = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  const service = options.service ?? createFqmApiService('real', 'http://localhost:8000');

  const config = {
    ...defaultConfig,
    ...options.config,
  };

  function Wrapper({ children }: PropsWithChildren) {
    return (
      <QueryClientProvider client={queryClient}>
        <AppConfigContext.Provider value={config}>
          <ApiServiceContext.Provider value={service}>
            <MemoryRouter initialEntries={[options.route ?? '/']}>
              {children}
            </MemoryRouter>
          </ApiServiceContext.Provider>
        </AppConfigContext.Provider>
      </QueryClientProvider>
    );
  }

  return render(ui, { wrapper: Wrapper });
}
