import { useId } from 'react'
import { cn } from './bits'

// Plip's sidebar icons: a crisp outline + a soft fill that turns brand-gradient when active.
type IconProps = { className?: string; active?: boolean }

function Duo({ className, active, children, accent }: IconProps & { children: React.ReactNode; accent: React.ReactNode }) {
  const id = useId()
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round"
      className={cn('shrink-0', className)} aria-hidden>
      <defs>
        <linearGradient id={id} x1="3" y1="3" x2="21" y2="21" gradientUnits="userSpaceOnUse">
          <stop stopColor="#9cc0ff" />
          <stop offset="1" stopColor="#5eead4" />
        </linearGradient>
      </defs>
      <g fill={active ? `url(#${id})` : '#8fb3fa'} fillOpacity={active ? 1 : 0.32} stroke="none">{accent}</g>
      {children}
    </svg>
  )
}

/** The notch: a screen with Plip's island on top. */
export function HomeIcon(props: IconProps) {
  return (
    <Duo {...props} accent={<rect x="8.5" y="4.6" width="7" height="3" rx="1.5" />}>
      <rect x="3" y="3.5" width="18" height="14" rx="3" />
      <path d="M9 20.5h6" />
    </Duo>
  )
}

/** Two switches. */
export function GeneralIcon(props: IconProps) {
  return (
    <Duo {...props} accent={<><circle cx="16" cy="7.5" r="2.6" /><circle cx="8" cy="16.5" r="2.6" /></>}>
      <rect x="3.5" y="4" width="17" height="7" rx="3.5" />
      <rect x="3.5" y="13" width="17" height="7" rx="3.5" />
    </Duo>
  )
}

/** A thought, with a spark in it. */
export function BrainIcon(props: IconProps) {
  return (
    <Duo {...props} accent={<path d="M12 6.6c.3 2 1.6 3.3 3.6 3.6-2 .3-3.3 1.6-3.6 3.6-.3-2-1.6-3.3-3.6-3.6 2-.3 3.3-1.6 3.6-3.6z" />}>
      <path d="M7 17.2A4.6 4.6 0 0 1 6.2 8a5.9 5.9 0 0 1 11.3-.3 4.8 4.8 0 0 1-.8 9.5z" />
      <circle cx="9" cy="20.6" r="1" />
    </Duo>
  )
}

/** A speech bubble that's talking. */
export function VoiceIcon(props: IconProps) {
  return (
    <Duo {...props} accent={<path d="M4 6.5A3.5 3.5 0 0 1 7.5 3h9A3.5 3.5 0 0 1 20 6.5v6a3.5 3.5 0 0 1-3.5 3.5H10l-4.5 4v-4.2A3.5 3.5 0 0 1 4 12.5z" />}>
      <path d="M4 6.5A3.5 3.5 0 0 1 7.5 3h9A3.5 3.5 0 0 1 20 6.5v6a3.5 3.5 0 0 1-3.5 3.5H10l-4.5 4v-4.2A3.5 3.5 0 0 1 4 12.5z" />
      <path d="M9 8v3M12 6.8v5.4M15 8.2v2.6" />
    </Duo>
  )
}

/** An eye, with your OK. */
export function PermissionsIcon(props: IconProps) {
  return (
    <Duo {...props} accent={<circle cx="12" cy="11" r="3" />}>
      <path d="M2.8 11C4.8 7.2 8.1 5 12 5s7.2 2.2 9.2 6c-2 3.8-5.3 6-9.2 6s-7.2-2.2-9.2-6z" />
      <path d="m15.5 19.2 1.8 1.8 3.4-3.4" />
    </Duo>
  )
}

/** A notebook with a ribbon. */
export function MemoryIcon(props: IconProps) {
  return (
    <Duo {...props} accent={<path d="M14 3h3v8l-1.5-1.2L14 11z" />}>
      <rect x="4.5" y="3" width="15" height="18" rx="3" />
      <path d="M8 3v18M11 15h5M11 18h3" />
    </Duo>
  )
}

/** Rising bars. */
export function ActivityIcon(props: IconProps) {
  return (
    <Duo {...props} accent={<rect x="15.5" y="5" width="4.5" height="15" rx="2.25" />}>
      <rect x="4" y="13" width="4.5" height="7" rx="2.25" />
      <rect x="9.75" y="9" width="4.5" height="11" rx="2.25" />
      <rect x="15.5" y="5" width="4.5" height="15" rx="2.25" />
    </Duo>
  )
}

/** Someone. */
export function AccountIcon(props: IconProps) {
  return (
    <Duo {...props} accent={<circle cx="12" cy="9" r="3.6" />}>
      <circle cx="12" cy="9" r="3.6" />
      <path d="M5 20c1.3-3.2 4-5 7-5s5.7 1.8 7 5" />
    </Duo>
  )
}
