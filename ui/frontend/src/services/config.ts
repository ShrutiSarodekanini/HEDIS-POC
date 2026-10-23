import type { AppRuntimeConfig, ConnectionMode } from '../types/api';

const API_BASE_URL_STORAGE_KEY = 'hedis.apiBaseUrl';

function normalizeApiBaseUrl(value: string | undefined | null): string {
  const fallback = 'http://localhost:8011/api';
  const raw = (value ?? '').trim();
  if (!raw) {
    return fallback;
  }

  try {
    const parsed = new URL(raw);
    const path = parsed.pathname.replace(/\/+$/, '');

    if (!path || path === '/') {
      parsed.pathname = '/api';
    } else if (path.toLowerCase() === '/api') {
      parsed.pathname = '/api';
    } else {
      parsed.pathname = path;
    }

    return parsed.toString().replace(/\/$/, '');
  } catch {
    const trimmed = raw.replace(/\/+$/, '');
    if (trimmed.toLowerCase().endsWith('/api')) {
      return trimmed;
    }
    return `${trimmed}/api`;
  }
}

function normalizeMode(_value: string | undefined): ConnectionMode {
  return 'real';
}

export function getDefaultConfig(): AppRuntimeConfig {
  const envApiBaseUrl = import.meta.env.VITE_API_BASE_URL;
  const envMode = import.meta.env.VITE_API_MODE;
  const envVersion = import.meta.env.VITE_APP_VERSION;

  return {
    apiBaseUrl: normalizeApiBaseUrl(envApiBaseUrl),
    mode: normalizeMode(envMode),
    version: envVersion ?? '0.1.0',
  };
}

export function getStoredApiBaseUrl(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }

  const value = window.localStorage.getItem(API_BASE_URL_STORAGE_KEY);
  return value ? normalizeApiBaseUrl(value) : null;
}

export function storeApiBaseUrl(value: string): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.setItem(API_BASE_URL_STORAGE_KEY, normalizeApiBaseUrl(value));
}

export function clearStoredApiBaseUrl(): void {
  if (typeof window === 'undefined') {
    return;
  }

  window.localStorage.removeItem(API_BASE_URL_STORAGE_KEY);
}
