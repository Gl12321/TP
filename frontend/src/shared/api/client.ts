let csrfToken = "";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function setCsrfToken(token: string) {
  csrfToken = token;
}

async function exchange<T>(
  path: string,
  options: RequestInit = {},
): Promise<{ payload: T; headers: Headers }> {
  const headers = new Headers(options.headers);
  const method = options.method ?? "GET";
  if (options.body && !(options.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken)
    headers.set("X-CSRF-Token", csrfToken);
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...options,
      headers,
      credentials: "include",
      redirect: "error",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new ApiError(
      0,
      "network",
      "Не удалось связаться с сервером. Проверьте подключение и повторите попытку.",
    );
  }
  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("json") ? await response.json() : null;
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("session-expired"));
    if (response.status === 403)
      window.dispatchEvent(new CustomEvent("access-revoked", { detail: path }));
    throw new ApiError(
      response.status,
      payload?.error?.code ?? "request_failed",
      payload?.error?.message ?? "Не удалось выполнить запрос.",
    );
  }
  if (response.status !== 204 && payload === null)
    throw new ApiError(
      response.status,
      "invalid_response",
      "Сервер вернул неожиданный ответ.",
    );
  return { payload: payload as T, headers: response.headers };
}

export async function request<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  return (await exchange<T>(path, options)).payload;
}

export type Page<T> = { items: T[]; nextCursor: string | null };

export async function getPage<T>(
  path: string,
  signal?: AbortSignal,
): Promise<Page<T>> {
  const response = await exchange<T[]>(path, { signal });
  return {
    items: response.payload,
    nextCursor: response.headers.get("X-Next-Cursor"),
  };
}

export const get = <T>(path: string, signal?: AbortSignal) =>
  request<T>(path, { signal });
export const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const workspacePath = (workspaceId: string, path = "") =>
  `/workspaces/${encodeURIComponent(workspaceId)}${path}`;
export function queryString(
  values: Record<string, string | number | string[] | null | undefined>,
) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "")
      params.set(key, Array.isArray(value) ? value.join(",") : String(value));
  });
  return params.size ? `?${params.toString()}` : "";
}
