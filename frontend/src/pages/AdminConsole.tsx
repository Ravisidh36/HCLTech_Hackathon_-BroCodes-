import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { ApiError, api } from '../api/client'
import type { AuditOut, Authority, DocumentOut, IngestResponse } from '../api/types'
import { AnswerBadge } from '../components/AnswerBadge'
import { useAuth } from '../auth'

type Tab = 'knowledge' | 'audit'

const AUTHORITIES: Array<{ value: Authority; label: string }> = [
  { value: 'regulatory', label: 'Regulatory (rank 1)' },
  { value: 'clinical_guideline', label: 'Clinical guideline (rank 2)' },
  { value: 'hospital_policy', label: 'Hospital policy (rank 3)' },
  { value: 'department_sop', label: 'Department SOP (rank 4)' },
]

function formatDateTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function AdminConsole() {
  const [tab, setTab] = useState<Tab>('knowledge')
  return (
    <div className="admin">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === 'knowledge'} className={tab === 'knowledge' ? 'tab is-active' : 'tab'} onClick={() => setTab('knowledge')}>
          Knowledge base
        </button>
        <button role="tab" aria-selected={tab === 'audit'} className={tab === 'audit' ? 'tab is-active' : 'tab'} onClick={() => setTab('audit')}>
          Audit trail
        </button>
      </div>
      {tab === 'knowledge' ? <KnowledgeTab /> : <AuditTab />}
    </div>
  )
}

// --- knowledge base ---------------------------------------------------------------------------

function KnowledgeTab() {
  const token = useAuth().session!.access_token
  const [docs, setDocs] = useState<DocumentOut[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setDocs(await api.documents(token))
      setLoadError(null)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Could not load documents')
    }
  }, [token])

  useEffect(() => {
    void load()
  }, [load])

  const byTopic = useMemo(() => {
    const groups = new Map<string, DocumentOut[]>()
    for (const d of docs) groups.set(d.topic, [...(groups.get(d.topic) ?? []), d])
    return [...groups.entries()]
  }, [docs])

  return (
    <div className="admin-grid">
      <UploadForm onUploaded={load} />
      <section className="card">
        <div className="card-head">
          <h2>Approved documents</h2>
          <span className="muted small">{docs.length} versions · {byTopic.length} topics</span>
        </div>
        {loadError && <p className="alert alert-error">{loadError}</p>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Document</th>
                <th>Version</th>
                <th>Effective</th>
                <th>Authority</th>
                <th>Status</th>
                <th className="num">Chunks</th>
                <th>Uploaded</th>
              </tr>
            </thead>
            <tbody>
              {byTopic.map(([topic, group]) => (
                <TopicRows key={topic} topic={topic} docs={group} />
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

function TopicRows({ topic, docs }: { topic: string; docs: DocumentOut[] }) {
  return (
    <>
      <tr className="group-row">
        <td colSpan={7}>{topic.replace(/_/g, ' ')}</td>
      </tr>
      {docs.map((d) => (
        <tr key={`${d.doc_id}-${d.version}`} className={d.status === 'superseded' ? 'is-muted' : ''}>
          <td>
            <div className="cell-title">{d.title}</div>
            <div className="mono muted small">{d.doc_id}</div>
          </td>
          <td>v{d.version}</td>
          <td>{d.effective_date}</td>
          <td>{d.authority.replace(/_/g, ' ')}</td>
          <td>
            <span className={`pill pill-${d.status}`}>{d.status}</span>
            {d.injection_flags > 0 && (
              <span className="pill pill-warn" title="Instruction-like text detected. Treated as data only.">
                ⚠ {d.injection_flags} flagged
              </span>
            )}
          </td>
          <td className="num">{d.chunk_count}</td>
          <td className="small">
            {d.uploaded_by}
            <div className="muted">{formatDateTime(d.uploaded_at)}</div>
          </td>
        </tr>
      ))}
    </>
  )
}

function UploadForm({ onUploaded }: { onUploaded: () => Promise<void> }) {
  const token = useAuth().session!.access_token
  const [result, setResult] = useState<IngestResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      setResult(await api.upload(token, new FormData(form)))
      await onUploaded()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Upload failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <div className="card-head">
        <h2>Upload a document</h2>
      </div>
      <p className="muted small">
        Searchable immediately, no restart. Version, authority and effective date come from this form, never from the
        document's own text.
      </p>
      <form className="form" onSubmit={(e) => void submit(e)}>
        <label className="field field-wide">
          <span>File (PDF, DOCX, Markdown)</span>
          <input type="file" name="file" required accept=".pdf,.docx,.pptx,.html,.htm,.md,.markdown,.txt" />
        </label>
        <label className="field">
          <span>Document ID</span>
          <input name="doc_id" required placeholder="POL-ICU-VISIT" pattern="[A-Z0-9][A-Z0-9-]{1,38}" />
        </label>
        <label className="field">
          <span>Version</span>
          <input name="version" required placeholder="3.0" pattern="\d+(\.\d+){0,3}" />
        </label>
        <label className="field field-wide">
          <span>Title</span>
          <input name="title" required placeholder="ICU Visiting Policy" />
        </label>
        <label className="field">
          <span>Topic</span>
          <input name="topic" required placeholder="icu_visiting" pattern="[a-z0-9_]{2,60}" />
        </label>
        <label className="field">
          <span>Effective date</span>
          <input name="effective_date" type="date" required />
        </label>
        <label className="field">
          <span>Authority</span>
          <select name="authority" defaultValue="hospital_policy">
            {AUTHORITIES.map((a) => (
              <option key={a.value} value={a.value}>
                {a.label}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Status</span>
          <select name="status" defaultValue="active">
            <option value="active">Active</option>
            <option value="draft">Draft</option>
          </select>
        </label>
        <div className="field-wide">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Indexing…' : 'Upload and index'}
          </button>
        </div>
      </form>

      {error && <p className="alert alert-error">{error}</p>}
      {result && (
        <div className={`alert ${result.injection_flags ? 'alert-warn' : 'alert-ok'}`}>
          <strong>
            {result.status === 'duplicate' ? 'Already indexed' : `Indexed ${result.doc_id} v${result.version}`}
          </strong>
          {result.status === 'indexed' && <span> · {result.chunks} chunks</span>}
          {result.superseded.length > 0 && <span> · superseded v{result.superseded.join(', v')}</span>}
          {result.injection_flags > 0 && (
            <div>⚠ {result.injection_flags} chunk(s) contain instruction-like text. They are stored as data only.</div>
          )}
          <div className="mono small muted">Trace {result.trace_id}</div>
        </div>
      )}
    </section>
  )
}

// --- audit ------------------------------------------------------------------------------------

const ENDPOINTS = ['', '/chat', '/actions/confirm', '/admin/documents', '/auth/login']

function AuditTab() {
  const token = useAuth().session!.access_token
  const [rows, setRows] = useState<AuditOut[]>([])
  const [endpoint, setEndpoint] = useState('')
  const [selected, setSelected] = useState<AuditOut | null>(null)
  const [traceQuery, setTraceQuery] = useState('')
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setRows(await api.recentAudit(token, endpoint || undefined))
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not load audit trail')
    }
  }, [token, endpoint])

  useEffect(() => {
    void load()
  }, [load])

  async function lookup(e: FormEvent) {
    e.preventDefault()
    if (!traceQuery.trim()) return
    try {
      const found = await api.auditByTrace(token, traceQuery.trim())
      setSelected(found[found.length - 1])
      setError(null)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Lookup failed')
    }
  }

  return (
    <div className="admin-grid audit-grid">
      <section className="card">
        <div className="card-head">
          <h2>Recent events</h2>
          <div className="row">
            <select value={endpoint} onChange={(e) => setEndpoint(e.target.value)} aria-label="Filter by endpoint">
              {ENDPOINTS.map((ep) => (
                <option key={ep} value={ep}>
                  {ep || 'All endpoints'}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-small" onClick={() => void load()}>
              Refresh
            </button>
          </div>
        </div>
        <form className="row" onSubmit={(e) => void lookup(e)}>
          <input value={traceQuery} onChange={(e) => setTraceQuery(e.target.value)} placeholder="Trace ID, e.g. HSP-4482876463" className="grow mono" />
          <button type="submit" className="btn btn-small">
            Look up
          </button>
        </form>
        {error && <p className="alert alert-error">{error}</p>}
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Time</th>
                <th>Endpoint</th>
                <th>Who</th>
                <th>Outcome</th>
                <th className="num">Latency</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.trace_id}-${i}`} className={selected?.trace_id === r.trace_id ? 'is-selected clickable' : 'clickable'} onClick={() => setSelected(r)}>
                  <td className="small">{formatDateTime(r.created_at)}</td>
                  <td className="mono small">{r.endpoint}</td>
                  <td className="small">{r.patient_id ?? r.role ?? 'anonymous'}</td>
                  <td>
                    {r.answer_type ? <AnswerBadge type={r.answer_type} /> : r.error ? <span className="pill pill-error">error</span> : <span className="muted small">—</span>}
                  </td>
                  <td className="num small">{r.latency_ms != null ? `${r.latency_ms} ms` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <section className="card">{selected ? <AuditDetail row={selected} /> : <p className="muted">Select an event to inspect its trace.</p>}</section>
    </div>
  )
}

function AuditDetail({ row }: { row: AuditOut }) {
  const maxMs = Math.max(1, ...row.node_timings.map((t) => t.ms))
  const tokens = row.token_usage.reduce((sum, t) => sum + Number(t.input_tokens ?? 0) + Number(t.output_tokens ?? 0), 0)
  return (
    <div className="audit-detail">
      <div className="card-head">
        <h2 className="mono">{row.trace_id}</h2>
        {row.answer_type && <AnswerBadge type={row.answer_type} />}
      </div>
      <dl className="kv">
        <dt>Endpoint</dt>
        <dd className="mono">{row.endpoint}</dd>
        <dt>When</dt>
        <dd>{new Date(row.created_at).toLocaleString()}</dd>
        <dt>User</dt>
        <dd>
          {row.role ?? '—'} {row.patient_id && <span className="mono">({row.patient_id})</span>}
        </dd>
        <dt>Request (PII masked)</dt>
        <dd>{row.question_redacted ?? '—'}</dd>
        <dt>Model</dt>
        <dd className="mono">{row.model ?? '—'}</dd>
        <dt>Tokens</dt>
        <dd>{tokens || '—'}</dd>
        <dt>Latency</dt>
        <dd>{row.latency_ms != null ? `${row.latency_ms} ms` : '—'}</dd>
        {row.error && (
          <>
            <dt>Notes</dt>
            <dd className="error-text">{row.error}</dd>
          </>
        )}
      </dl>

      {row.privacy_checks.length > 0 && (
        <>
          <h3>Privacy and safety checks</h3>
          <ul className="checks">
            {row.privacy_checks.map((c) => (
              <li key={c.check} className={c.passed ? 'pass' : 'fail'}>
                {c.passed ? '✓' : '✗'} {c.check.replace(/_/g, ' ')}
              </li>
            ))}
          </ul>
        </>
      )}

      {row.node_timings.length > 0 && (
        <>
          <h3>Workflow timing</h3>
          <ul className="bars">
            {row.node_timings.map((t, i) => (
              <li key={i}>
                <span className="bar-label mono">{t.node}</span>
                <span className="bar-track">
                  <span className="bar-fill" style={{ width: `${(t.ms / maxMs) * 100}%` }} />
                </span>
                <span className="bar-value">{t.ms} ms</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {row.tools.length > 0 && (
        <>
          <h3>Tools invoked</h3>
          <ul className="tools">
            {row.tools.map((t, i) => (
              <li key={i} className={`tool tool-${String(t.status)}`}>
                <span className="mono">{String(t.tool)}</span>
                <span className="tool-status">{String(t.status)}</span>
              </li>
            ))}
          </ul>
        </>
      )}

      {row.sources.length > 0 && (
        <>
          <h3>Sources retrieved</h3>
          <ul className="plain-list small mono">
            {row.sources.map((s, i) => (
              <li key={i}>
                {String(s.doc_id ?? '')} {s.version ? `v${String(s.version)}` : ''} {s.chunk_id ? `· ${String(s.chunk_id)}` : ''}
                {s.score != null ? ` · score ${String(s.score)}` : ''}
                {s.result ? ` · ${String(s.result)}` : ''}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
