import type { Citation } from '../api/types'

function formatDate(iso: string) {
  const d = new Date(`${iso}T00:00:00`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

/** Compact chips under an answer. */
export function CitationChips({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null
  return (
    <ul className="chips" aria-label="Sources">
      {citations.map((c, i) =>
        c.kind === 'document' ? (
          <li key={i} className="chip chip-doc" title={`${c.document} · ${c.section} · v${c.version} · effective ${c.effective_date}`}>
            <span className="chip-icon" aria-hidden>
              §
            </span>
            {c.document} · v{c.version}
          </li>
        ) : (
          <li key={i} className="chip chip-system">
            <span className="chip-icon" aria-hidden>
              ⛁
            </span>
            {c.system}
          </li>
        ),
      )}
    </ul>
  )
}

/** Full citation list for the details panel (R2: document, section, version, effective date). */
export function CitationList({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return <p className="muted small">No sources for this answer.</p>
  return (
    <ol className="citation-list">
      {citations.map((c, i) =>
        c.kind === 'document' ? (
          <li key={i}>
            <div className="citation-title">{c.document}</div>
            <div className="citation-meta">
              <span>{c.section}</span>
              <span>Version {c.version}</span>
              <span>Effective {formatDate(c.effective_date)}</span>
              {c.page != null && <span>Page {c.page}</span>}
            </div>
            <div className="citation-id">{c.doc_id}</div>
          </li>
        ) : (
          <li key={i}>
            <div className="citation-title">{c.system}</div>
            <div className="citation-meta">
              <span>Live hospital system</span>
            </div>
          </li>
        ),
      )}
    </ol>
  )
}
