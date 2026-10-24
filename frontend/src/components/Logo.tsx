import { useId } from 'react'

/** Nirog mark: medical cross with a heartbeat line and a leaf for health. Same art as public/favicon.svg. */
export function Logo({ size = 32 }: { size?: number }) {
  const gradient = useId()
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" role="img" aria-label="Nirog logo" className="logo">
      <defs>
        <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#14b8a6" />
          <stop offset="1" stopColor="#0f766e" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="12" fill={`url(#${gradient})`} />
      <rect x="19" y="10" width="10" height="28" rx="3" fill="#fff" />
      <rect x="10" y="19" width="28" height="10" rx="3" fill="#fff" />
      <path
        d="M12 24h7l2.2-4.2 3.6 8.4 2.2-4.2h9"
        fill="none"
        stroke="#0f766e"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M31 17.5c.4-5.2 3.9-8.3 9-8.5-.2 5.3-3.6 8.6-9 8.5z" fill="#bbf7d0" />
      <path d="M31 17.5c2-2.3 4.1-4.2 6.4-5.6" fill="none" stroke="#0f766e" strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  )
}
