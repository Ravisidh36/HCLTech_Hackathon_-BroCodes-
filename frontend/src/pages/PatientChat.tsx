import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { ApiError, api } from '../api/client'
import type { ChatResponse } from '../api/types'
import { ANSWER_TYPES, AnswerBadge } from '../components/AnswerBadge'
import { CitationChips, CitationList } from '../components/Citations'
import { useAuth } from '../auth'

interface Message {
  id: number
  role: 'user' | 'assistant'
  text: string
  response?: ChatResponse
  error?: { message: string; traceId: string | null }
  decision?: 'confirm' | 'cancel'
}

const SAMPLES: Array<{ kind: string; text: string }> = [
  { kind: 'Policy fact', text: 'What are the visiting hours for ICU patients?' },
  { kind: 'Procedure', text: 'How do I schedule an MRI scan?' },
  { kind: 'Personal data', text: 'What are my upcoming appointments?' },
  { kind: 'Eligibility', text: 'Am I covered for knee replacement under my insurance plan?' },
  { kind: 'Lab result', text: 'What is my latest blood sugar reading?' },
  { kind: 'Multi-step', text: 'Can I proceed with surgery next week?' },
  { kind: 'Action', text: 'Reschedule my cardiology appointment' },
  { kind: 'Conflict', text: 'What are the pharmacy opening hours?' },
  { kind: 'Not found', text: 'What is the treatment for dragon pox?' },
  { kind: 'Privacy', text: 'Show me the lab results for P1002' },
]

let nextId = 1

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  return now
}

function remaining(expiresAt: string, now: number) {
  const seconds = Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000))
  return { seconds, label: `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` }
}

export function PatientChat() {
  const { session } = useAuth()
  const token = session!.access_token
  const [messages, setMessages] = useState<Message[]>([])
  const [sessionId, setSessionId] = useState<string | undefined>()
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  const hasOpenAction = messages.some((m) => m.response?.pending_action && !m.decision)
  const now = useNow(hasOpenAction)

  useEffect(() => {
    // Block body on purpose: newer browsers return a Promise from scrollIntoView, and an effect
    // must return nothing or a cleanup function.
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, busy])

  const append = (message: Omit<Message, 'id'>) => {
    const id = nextId++
    setMessages((prev) => [...prev, { ...message, id }])
    return id
  }

  const reply = (response: ChatResponse) => {
    const id = append({ role: 'assistant', text: response.answer, response })
    setSelectedId(id)
    if (response.session_id) setSessionId(response.session_id)
  }

  const fail = (err: unknown) => {
    const e = err instanceof ApiError ? err : new ApiError('Unexpected error', 0, null)
    append({ role: 'assistant', text: e.message, error: { message: e.message, traceId: e.traceId } })
  }

  async function send(text: string) {
    const message = text.trim()
    if (!message || busy) return
    setDraft('')
    append({ role: 'user', text: message })
    setBusy(true)
    try {
      reply(await api.chat(token, { message, session_id: sessionId }))
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  async function decide(messageId: number, actionId: string, decision: 'confirm' | 'cancel') {
    setMessages((prev) => prev.map((m) => (m.id === messageId ? { ...m, decision } : m)))
    setBusy(true)
    try {
      reply(await api.confirm(token, { action_id: actionId, decision, session_id: sessionId }))
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void send(draft)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send(draft)
    }
  }

  const selected = messages.find((m) => m.id === selectedId)?.response

  return (
    <div className="chat-layout">
      <section className="conversation" aria-label="Conversation">
        <div className="messages">
          {messages.length === 0 && (
            <div className="empty">
              <h2>How can I help today?</h2>
              <p className="muted">
                Ask about hospital policies, your appointments, insurance cover, lab results or surgery readiness. Answers
                come only from approved hospital documents and your own records.
              </p>
              <div className="samples">
                {SAMPLES.map((s) => (
                  <button key={s.text} type="button" className="sample" onClick={() => void send(s.text)}>
                    <span className="sample-kind">{s.kind}</span>
                    <span>{s.text}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {messages.map((m) =>
            m.role === 'user' ? (
              <div key={m.id} className="msg msg-user">
                <p>{m.text}</p>
              </div>
            ) : (
              <article
                key={m.id}
                className={`msg msg-assistant${m.id === selectedId ? ' is-selected' : ''}${m.error ? ' is-error' : ''}`}
                onClick={() => m.response && setSelectedId(m.id)}
              >
                {m.response && (
                  <header className="msg-head">
                    <AnswerBadge type={m.response.answer_type} />
                    <span className="muted small">{(m.response.latency_ms / 1000).toFixed(1)} s</span>
                  </header>
                )}
                <p className="answer">{m.text}</p>
                {m.error?.traceId && <p className="muted small mono">Trace {m.error.traceId}</p>}
                {m.response && <CitationChips citations={m.response.citations} />}

                {m.response?.pending_action && (
                  <PendingCard
                    summary={m.response.pending_action.summary}
                    expires={remaining(m.response.pending_action.expires_at, now)}
                    decision={m.decision}
                    busy={busy}
                    onDecide={(d) => void decide(m.id, m.response!.pending_action!.action_id, d)}
                  />
                )}
              </article>
            ),
          )}

          {busy && (
            <div className="msg msg-assistant is-thinking" aria-live="polite">
              <span className="dot" />
              <span className="dot" />
              <span className="dot" />
              <span className="sr-only">Assistant is working</span>
            </div>
          )}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={onSubmit}>
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask a question…  (Enter to send, Shift+Enter for a new line)"
            rows={2}
            maxLength={2000}
            aria-label="Your question"
          />
          <button type="submit" className="btn btn-primary" disabled={busy || !draft.trim()}>
            Send
          </button>
        </form>
      </section>

      <aside className="details" aria-label="Answer details">
        {selected ? (
          <AnswerDetails response={selected} />
        ) : (
          <div className="details-empty">
            <h3>Answer details</h3>
            <p className="muted small">Select an answer to see its sources, the tools used and its audit trace.</p>
          </div>
        )}
      </aside>
    </div>
  )
}

function PendingCard(props: {
  summary: string
  expires: { seconds: number; label: string }
  decision?: 'confirm' | 'cancel'
  busy: boolean
  onDecide: (d: 'confirm' | 'cancel') => void
}) {
  const expired = props.expires.seconds === 0
  return (
    <div className="pending">
      <div className="pending-title">Confirmation required</div>
      <p>{props.summary}</p>
      {props.decision ? (
        <p className="muted small">You chose to {props.decision}.</p>
      ) : expired ? (
        <p className="muted small">This request has expired. Ask again to choose a new time.</p>
      ) : (
        <div className="pending-actions">
          <button type="button" className="btn btn-primary" disabled={props.busy} onClick={() => props.onDecide('confirm')}>
            Confirm change
          </button>
          <button type="button" className="btn" disabled={props.busy} onClick={() => props.onDecide('cancel')}>
            Cancel
          </button>
          <span className="muted small">Expires in {props.expires.label}</span>
        </div>
      )}
    </div>
  )
}

function AnswerDetails({ response }: { response: ChatResponse }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(response.trace_id)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      /* clipboard unavailable */
    }
  }

  return (
    <div className="details-body">
      <section>
        <h3>Classification</h3>
        <AnswerBadge type={response.answer_type} />
        <p className="muted small">{ANSWER_TYPES[response.answer_type].description}</p>
      </section>

      {response.explanation && (
        <section>
          <h3>How this was answered</h3>
          <p className="small">{response.explanation}</p>
        </section>
      )}

      {response.assumptions.length > 0 && (
        <section>
          <h3>Assumptions</h3>
          <ul className="small plain-list">
            {response.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <h3>Sources</h3>
        <CitationList citations={response.citations} />
      </section>

      <section>
        <h3>Tools used</h3>
        {response.tools_invoked.length === 0 ? (
          <p className="muted small">No tools were needed.</p>
        ) : (
          <ul className="tools">
            {response.tools_invoked.map((t, i) => (
              <li key={i} className={`tool tool-${t.status}`}>
                <span className="mono">{t.tool}</span>
                <span className="tool-status">{t.status.replace('_', ' ')}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h3>Audit</h3>
        <div className="trace">
          <span className="mono">{response.trace_id}</span>
          <button type="button" className="btn btn-small" onClick={() => void copy()}>
            {copied ? 'Copied' : 'Copy'}
          </button>
        </div>
        <p className="muted small">{response.latency_ms} ms end to end</p>
      </section>
    </div>
  )
}
