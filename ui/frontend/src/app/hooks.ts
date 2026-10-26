import { useContext } from 'react';
import { ApiServiceContext, AppConfigContext } from './contexts';
import type { AppConfigContextValue } from './contexts';
import type { FqmApiService } from '../services/fqmApiService';

export function useAppConfig(): AppConfigContextValue {
  const context = useContext(AppConfigContext);
  if (!context) {
    throw new Error('useAppConfig must be used within AppProviders.');
  }

  return context;
}

export function useFqmApiService(): FqmApiService {
  const context = useContext(ApiServiceContext);
  if (!context) {
    throw new Error('useFqmApiService must be used within AppProviders.');
  }

  return context;
}
