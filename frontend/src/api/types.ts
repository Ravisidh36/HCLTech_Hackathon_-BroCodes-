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
  session_id: string
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
  session_id?: string
}

export interface MeResponse {
  username: string
  full_name: string
  role: Role
  patient_id: string | null
}

export type Authority = 'regulatory' | 'clinical_guideline' | 'hospital_policy' | 'department_sop'
export type DocStatus = 'active' | 'superseded' | 'draft'

export interface DocumentOut {
  doc_id: string
  title: string
  topic: string
  version: string
  effective_date: string
  authority: Authority
  status: DocStatus
  chunk_count: number
  injection_flags: number
  uploaded_by: string
  uploaded_at: string
  source_filename: string
}

export interface IngestResponse {
  status: 'indexed' | 'duplicate'
  doc_id: string
  version: string
  chunks: number
  injection_flags: number
  superseded: string[]
  message: string
  trace_id: string
}

export interface PrivacyCheck {
  check: string
  passed: boolean
}

export interface NodeTiming {
  node: string
  ms: number
}

export interface AuditOut {
  trace_id: string
  endpoint: string
  created_at: string
  user_id: number | null
  role: Role | null
  patient_id: string | null
  question_redacted: string | null
  answer_type: AnswerType | null
  sources: Array<Record<string, unknown>>
  tools: Array<Record<string, unknown>>
  privacy_checks: PrivacyCheck[]
  node_timings: NodeTiming[]
  model: string | null
  token_usage: Array<Record<string, unknown>>
  latency_ms: number | null
  error: string | null
}
