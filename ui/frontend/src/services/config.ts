import type { AppRuntimeConfig, ConnectionMode } from '../types/api';

const API_BASE_URL_STORAGE_KEY = 'hedis.apiBaseUrl';

function normalizeMode(_value: string | undefined): ConnectionMode {
  return 'real';
}

export function getDefaultConfig(): AppRuntimeConfig {
  const envApiBaseUrl = import.meta.env.VITE_API_BASE_URL;
  const envMode = import.meta.env.VITE_API_MODE;
  const envVersion = import.meta.env.VITE_APP_VERSION;

  return {
    apiBaseUrl: envApiBaseUrl ?? 'http://localhost:8011/api',
    mode: normalizeMode(envMode),
    version: envVersion ?? '0.1.0',
  };
}

export function getStoredApiBaseUrl(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }

  return window.localStorage.getItem(API_BASE_URL_STORAGE_KEY);
}

export function storeApiBaseUrl(value: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(API_BASE_URL_STORAGE_KEY, value);
}

export function clearStoredApiBaseUrl(): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.removeItem(API_BASE_URL_STORAGE_KEY);
}
