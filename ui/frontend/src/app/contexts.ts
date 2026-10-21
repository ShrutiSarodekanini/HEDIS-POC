import { createContext } from 'react';
import type { FqmApiService } from '../services/fqmApiService';
import type { AppRuntimeConfig } from '../types/api';

export interface AppConfigContextValue extends AppRuntimeConfig {
  setApiBaseUrl: (value: string) => void;
  resetApiBaseUrl: () => void;
}

export const AppConfigContext = createContext<AppConfigContextValue | null>(null);
export const ApiServiceContext = createContext<FqmApiService | null>(null);
