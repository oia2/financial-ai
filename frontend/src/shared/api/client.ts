/**
 * HTTP-клиент Backend-API.
 *
 * Ключевая ответственность — различить два класса сбоев (FR-037):
 *
 *  - `ServerUnreachableError` — запрос к серверу Financial AI не завершился
 *    (сеть, таймаут, 502/503/504 от nginx без нашего тела ответа);
 *  - `ApiError` — сервер ответил, и ответ описывает прикладную ошибку.
 *
 * Недоступность T-Bank API ошибкой транспорта НЕ является: она приходит
 * как успешный ответ 200 с `sync.status = "failed"`.
 */

export class ServerUnreachableError extends Error {
  override readonly cause?: unknown;

  constructor(message = 'Нет связи с сервером Financial AI', cause?: unknown) {
    super(message);
    this.name = 'ServerUnreachableError';
    this.cause = cause;
  }
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string | undefined;
  readonly detail: unknown;

  constructor(status: number, code: string | undefined, detail: unknown) {
    super(`Запрос завершился с кодом ${status}${code ? ` (${code})` : ''}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

/** Коды, которыми nginx отвечает, когда backend-api недоступен. */
const GATEWAY_STATUSES = new Set([502, 503, 504]);

interface ErrorEnvelope {
  detail?: { code?: string } | string;
}

function extractCode(body: unknown): string | undefined {
  if (typeof body !== 'object' || body === null) return undefined;
  const detail = (body as ErrorEnvelope).detail;
  if (typeof detail === 'object' && detail !== null && typeof detail.code === 'string') {
    return detail.code;
  }
  return undefined;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;

  try {
    response = await fetch(path, {
      ...init,
      headers: {
        Accept: 'application/json',
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    });
  } catch (cause) {
    // fetch отвергает промис только при сетевом сбое — сервер недоступен.
    throw new ServerUnreachableError(undefined, cause);
  }

  let body: unknown = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null;
    }
  }

  if (!response.ok) {
    const code = extractCode(body);

    // 502/503/504 без нашего тела ответа — это nginx, а не приложение:
    // сервер Financial AI недоступен.
    if (GATEWAY_STATUSES.has(response.status) && code === undefined) {
      throw new ServerUnreachableError();
    }

    throw new ApiError(response.status, code, body);
  }

  return body as T;
}

export function apiGet<T>(path: string): Promise<T> {
  return request<T>(path);
}

export function apiPost<T>(path: string, payload?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
  });
}

export function apiPut<T>(path: string, payload: unknown): Promise<T> {
  return request<T>(path, { method: 'PUT', body: JSON.stringify(payload) });
}
