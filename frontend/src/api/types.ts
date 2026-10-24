// Mirrors app/schemas/*.py. Keep in sync with the backend contracts.

export type AnswerType =
  | 'retrieved_fact'
  | 'calculated'
  | 'action_completed'
  | 'clarification_needed'
  | 'not_found'
  | 'refused'
  | 'conflict_flagged'

export type Role = 'patient' | 'admin'

export type ToolStatus = 'ok' | 'not_found' | 'error'

export interface DocCitation {
  kind: 'document'
  document: string
  doc_id: string
  section: string
  version: string
  effective_date: string
  page: number | null
}

export interface SystemCitation {
  kind: 'system'
  system: string
}

export type Citation = DocCitation | SystemCitation

export interface ToolInvocation {
  tool: string
  status: ToolStatus
}

export interface PendingAction {
  action_id: string
  summary: string
  expires_at: string
}

export interface ChatResponse {
  trace_id: string
  answer: string
  answer_type: AnswerType
  citations: Citation[]
  tools_invoked: ToolInvocation[]
  explanation: string
  assumptions: string[]
  pending_action: PendingAction | null
  latency_ms: number
}

export interface LoginRequest {
  username: string
  password: string
}

export interface TokenResponse {
  access_token: string
  token_type: 'bearer'
  role: Role
  patient_id: string | null
  full_name: string
}

export interface ChatRequest {
  message: string
  session_id?: string
}

export interface ConfirmRequest {
  action_id: string
  decision: 'confirm' | 'cancel'
}
