import type { ConnectionMode } from '../types/api';
import type { FqmApiService } from './fqmApiService';
import { RealFqmApiService } from './realFqmApiService';

export function createFqmApiService(
  _mode: ConnectionMode,
  baseUrl: string,
): FqmApiService {
  return new RealFqmApiService(baseUrl);
}
