export class ApiError extends Error {
  status: number;
  details: unknown;

  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.details = details;
  }
}

function trimTrailingSlash(input: string): string {
  return input.endsWith('/') ? input.slice(0, -1) : input;
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? '';

  if (contentType.includes('application/json')) {
    return response.json();
  }

  const textBody = await response.text();
  return textBody.length > 0 ? textBody : null;
}

function toSearchParams(query?: Record<string, unknown>): string {
  if (!query) {
    return '';
  }

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') {
      continue;
    }

    params.set(key, String(value));
  }

  const serialized = params.toString();
  return serialized.length > 0 ? `?${serialized}` : '';
}

export class ApiClient {
  private readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = trimTrailingSlash(baseUrl);
  }

  async get<TResponse>(
    path: string,
    query?: Record<string, unknown>,
  ): Promise<TResponse> {
    return this.request<TResponse>(path + toSearchParams(query), {
      method: 'GET',
    });
  }

  async post<TResponse>(
    path: string,
    body: unknown,
    init?: RequestInit,
  ): Promise<TResponse> {
    return this.request<TResponse>(path, {
      method: 'POST',
      body: JSON.stringify(body),
      headers: {
        'content-type': 'application/json',
      },
      ...init,
    });
  }

  async postForm<TResponse>(path: string, body: FormData): Promise<TResponse> {
    return this.request<TResponse>(path, {
      method: 'POST',
      body,
    });
  }

  private async request<TResponse>(
    path: string,
    init: RequestInit,
  ): Promise<TResponse> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init.headers ?? {}),
      },
    });

    const parsedBody = await parseResponseBody(response);

    if (!response.ok) {
      throw new ApiError(
        `Request failed with status ${response.status}`,
        response.status,
        parsedBody,
      );
    }

    return parsedBody as TResponse;
  }
}
