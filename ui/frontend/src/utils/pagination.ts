import type { PaginatedResponse } from '../types/api';

export function paginate<TItem>(
  items: TItem[],
  page = 1,
  pageSize = 10,
): PaginatedResponse<TItem> {
  const safePage = Math.max(1, page);
  const safeSize = Math.max(1, pageSize);
  const startIndex = (safePage - 1) * safeSize;

  return {
    items: items.slice(startIndex, startIndex + safeSize),
    page: safePage,
    pageSize: safeSize,
    total: items.length,
  };
}
