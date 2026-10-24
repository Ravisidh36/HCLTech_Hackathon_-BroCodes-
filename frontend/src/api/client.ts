import type {
  AuditOut,
  ChatRequest,
  ChatResponse,
  ConfirmRequest,
  DocumentOut,
  IngestResponse,
  LoginRequest,
  TokenResponse,
} from './types'

export class ApiError extends Error {
  status: number
  traceId: string | null

  constructor(message: string, status: number, traceId: string | null) {
    super(message)
    this.status = status
    this.traceId = traceId
  }
}

/** FastAPI errors arrive as a string, an object with a message, or a list of validation errors. */
function errorMessage(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        const item = d as { loc?: unknown[]; msg?: string }
        const field = item.loc?.filter((p) => p !== 'body').join('.') ?? ''
        return field ? `${field}: ${item.msg}` : (item.msg ?? 'Invalid input')
      })
      .join('; ')
  }
  if (detail && typeof detail === 'object' && 'message' in detail) return String((detail as { message: unknown }).message)
  if (status === 429) return 'Too many requests. Please wait a moment and try again.'
  return `Request failed (${status})`
}

let onUnauthorized: () => void = () => {}
export function setUnauthorizedHandler(handler: () => void) {
  onUnauthorized = handler
}

async function request<T>(path: string, init: RequestInit & { token?: string | null } = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.token) headers.set('Authorization', `Bearer ${init.token}`)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')

  let response: Response
  try {
    response = await fetch(`/api${path}`, { ...init, headers })
  } catch {
    throw new ApiError('Cannot reach the server. Is the backend running on port 8000?', 0, null)
  }

  const traceId = response.headers.get('X-Trace-Id')
  if (response.ok) return (await response.json()) as T

  let detail: unknown = null
  try {
    detail = ((await response.json()) as { detail?: unknown }).detail
  } catch {
    /* non-JSON error body */
  }
  if (response.status === 401 && init.token) onUnauthorized()
  const nestedTrace = detail && typeof detail === 'object' && 'trace_id' in detail ? String((detail as { trace_id: unknown }).trace_id) : null
  throw new ApiError(errorMessage(detail, response.status), response.status, nestedTrace ?? traceId)
}

export const api = {
  login: (body: LoginRequest) => request<TokenResponse>('/auth/login', { method: 'POST', body: JSON.stringify(body) }),

  chat: (token: string, body: ChatRequest) =>
    request<ChatResponse>('/chat', { method: 'POST', body: JSON.stringify(body), token }),

  confirm: (token: string, body: ConfirmRequest) =>
    request<ChatResponse>('/actions/confirm', { method: 'POST', body: JSON.stringify(body), token }),

  documents: (token: string) => request<DocumentOut[]>('/admin/documents', { token }),

  upload: (token: string, form: FormData) =>
    request<IngestResponse>('/admin/documents', { method: 'POST', body: form, token }),

  recentAudit: (token: string, endpoint?: string) =>
    request<AuditOut[]>(`/admin/audit?limit=60${endpoint ? `&endpoint=${encodeURIComponent(endpoint)}` : ''}`, { token }),

  auditByTrace: (token: string, traceId: string) =>
    request<AuditOut[]>(`/admin/audit/${encodeURIComponent(traceId)}`, { token }),
}
