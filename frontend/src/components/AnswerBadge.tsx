import type { AnswerType } from '../api/types'

export const ANSWER_TYPES: Record<AnswerType, { label: string; description: string }> = {
  retrieved_fact: { label: 'Retrieved fact', description: 'Supported directly by approved hospital documents.' },
  calculated: { label: 'Calculated', description: 'Produced by a deterministic hospital-system tool.' },
  action_completed: { label: 'Action completed', description: 'An authorised workflow was executed.' },
  clarification_needed: { label: 'Needs your input', description: 'More information or a confirmation is required.' },
  not_found: { label: 'Not found', description: 'Authorised sources do not contain the answer.' },
  refused: { label: 'Refused', description: 'The request is unauthorised or unsafe.' },
  conflict_flagged: { label: 'Conflict flagged', description: 'Policies conflict and cannot be resolved automatically.' },
}

export function AnswerBadge({ type }: { type: AnswerType }) {
  return (
    <span className={`badge badge-${type}`} title={ANSWER_TYPES[type].description}>
      {ANSWER_TYPES[type].label}
    </span>
  )
}
