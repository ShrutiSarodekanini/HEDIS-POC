import {
  QueryClient,
  QueryClientProvider,
} from '@tanstack/react-query';
import {
  type PropsWithChildren,
  useMemo,
  useState,
} from 'react';
import { createFqmApiService } from '../services/createFqmApiService';
import {
  clearStoredApiBaseUrl,
  getDefaultConfig,
  getStoredApiBaseUrl,
  storeApiBaseUrl,
} from '../services/config';
import { ApiServiceContext, AppConfigContext } from './contexts';
import type { AppConfigContextValue } from './contexts';

const defaults = getDefaultConfig();

export function AppProviders({ children }: PropsWithChildren) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  const [apiBaseUrl, setApiBaseUrlState] = useState(() => {
    const envApiBaseUrl = import.meta.env.VITE_API_BASE_URL;
    if (typeof envApiBaseUrl === 'string' && envApiBaseUrl.trim().length > 0) {
      return envApiBaseUrl;
    }

    return getStoredApiBaseUrl() ?? defaults.apiBaseUrl;
  });

  const configValue = useMemo<AppConfigContextValue>(
    () => ({
      apiBaseUrl,
      mode: defaults.mode,
      version: defaults.version,
      setApiBaseUrl: (value: string) => {
        setApiBaseUrlState(value);
        storeApiBaseUrl(value);
        queryClient.clear();
      },
      resetApiBaseUrl: () => {
        setApiBaseUrlState(defaults.apiBaseUrl);
        clearStoredApiBaseUrl();
        queryClient.clear();
      },
    }),
    [apiBaseUrl, queryClient],
  );

  const service = useMemo(
    () => createFqmApiService(configValue.mode, configValue.apiBaseUrl),
    [configValue.mode, configValue.apiBaseUrl],
  );

  return (
    <QueryClientProvider client={queryClient}>
      <AppConfigContext.Provider value={configValue}>
        <ApiServiceContext.Provider value={service}>
          {children}
        </ApiServiceContext.Provider>
      </AppConfigContext.Provider>
    </QueryClientProvider>
  );
}
