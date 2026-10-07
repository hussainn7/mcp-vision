import { AnimatePresence, motion } from 'motion/react'
import { LoaderCircle, Lock } from 'lucide-react'
import { send, type AccountState, type SettingsState } from '../../bridge'
import { Mascot } from '../../components/Mascot'
import { cn } from '../../components/bits'
import { PRIVACY_URL } from './onboarding'
import { Button, Card, Header, Pill, Row, Rows, Section } from './ui'

type User = NonNullable<AccountState['user']>

/** Google's "G", as its sign-in buttons show it. */
export function GoogleMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" aria-hidden="true" className={cn('size-5 shrink-0', className)}>
      <path fill="#FFC107" d="M43.6 20.1H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 8 3l5.7-5.7C34 6.1 29.3 4 24 4 13 4 4 13 4 24s9 20 20 20 20-9 20-20c0-1.3-.1-2.6-.4-3.9z" />
      <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 8 3l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
      <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-7.9l-6.5 5C9.5 39.6 16.2 44 24 44z" />
      <path fill="#1976D2" d="M43.6 20.1H42V20H24v8h11.3c-.8 2.2-2.2 4.2-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.6-.4-3.9z" />
    </svg>
  )
}

/** Their initial in a circle: no picture is fetched or kept. */
export function Avatar({ user, size = 20, className }: { user: User; size?: number; className?: string }) {
  const initial = (user.name || user.email || '?').trim().charAt(0).toUpperCase()
  return (
    <span
      className={cn('grid shrink-0 place-items-center rounded-full bg-gradient-to-br from-plip-300 to-plip-600 font-semibold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.35)]', className)}
      style={{ width: size, height: size, fontSize: Math.round(size * 0.46) }}
    >
      {initial}
    </span>
  )
}

/** The first thing anyone sees: sign in with Google, then the tour (or straight to Plip). */
export function SignIn({ state }: { state: SettingsState }) {
  const { status, error } = state.account
  const waiting = status === 'waiting'
  const failed = status === 'failed'
  return (
    <div className="relative flex h-full flex-col overflow-hidden bg-ink text-white noise">
      <div className="pointer-events-none absolute -left-48 -top-64 size-[560px] rounded-full bg-plip-500/[0.14] blur-[130px] animate-aurora" />
      <div className="pointer-events-none absolute -right-56 top-56 size-[460px] rounded-full bg-sky-glow/[0.08] blur-[130px] animate-aurora [animation-delay:-7s]" />

      <main className="relative z-10 flex flex-1 items-center justify-center px-8">
        <motion.div
          initial={{ opacity: 0, y: 10, filter: 'blur(6px)' }}
          animate={{ opacity: 1, y: 0, filter: 'blur(0px)' }}
          transition={{ duration: 0.4, ease: [0.32, 0.72, 0, 1] }}
          className="flex w-full max-w-[440px] flex-col items-center text-center"
        >
          <div className="relative mb-8">
            <div className="absolute inset-0 -z-10 scale-[1.9] rounded-full bg-plip-400/25 blur-3xl" />
            <Mascot size={92} mood={waiting ? 'thinking' : failed ? 'error' : 'happy'} />
          </div>
          <h1 className="text-gradient text-[36px] font-semibold leading-none tracking-[-0.045em]">Welcome to Plip.</h1>
          <p className="mt-3 text-[15px] text-white/50">A little helper that lives in your notch.</p>

          <button
            onClick={() => send('account-sign-in', { provider: 'google' })}
            disabled={waiting}
            className={cn(
              'mt-12 inline-flex h-12 w-full max-w-[320px] items-center justify-center gap-3 rounded-full bg-white px-6 text-[15px] font-semibold tracking-tight text-slate-950 transition',
              'shadow-[inset_0_-2px_0_rgba(0,0,0,0.08),0_10px_30px_-10px_rgba(156,194,250,0.55)] hover:bg-plip-50 active:scale-[0.98] disabled:cursor-default disabled:opacity-70 disabled:active:scale-100',
            )}
          >
            {waiting ? <LoaderCircle className="size-5 animate-spin text-plip-500" /> : <GoogleMark />}
            {waiting ? 'Waiting for Google…' : failed ? 'Try again with Google' : 'Continue with Google'}
          </button>

          <div className="mt-4 min-h-[44px]">
            <AnimatePresence mode="wait">
              {waiting && (
                <motion.div key="waiting" initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="text-[12.5px] text-white/45">
                  Finish signing in in your browser, then come back here.
                  <div className="mt-1.5 flex items-center justify-center gap-1">
                    <Button size="sm" variant="quiet" onClick={() => send('account-open')}>Open the page again</Button>
                    <span className="text-white/20">·</span>
                    <Button size="sm" variant="quiet" onClick={() => send('account-cancel')}>Cancel</Button>
                  </div>
                </motion.div>
              )}
              {failed && (
                <motion.div key="failed" role="alert" initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                  className="max-w-[360px] text-[12.5px] leading-relaxed text-rose-300/90">
                  {error || 'Sign-in didn’t finish. Try again.'}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </motion.div>
      </main>

      <footer className="relative z-10 flex items-center justify-center gap-1.5 px-8 pb-7 text-[11.5px] text-white/35">
        <Lock className="size-3.5 shrink-0 text-mint" />
        Your account is your name and email. What you ask, your screen and your memory stay on this Mac.
        <button className="text-white/45 underline-offset-2 hover:text-white/75 hover:underline" onClick={() => send('open-url', { url: PRIVACY_URL })}>Privacy</button>
      </footer>
    </div>
  )
}

function joined(since: number | null): string {
  return since ? new Date(since * 1000).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' }) : ''
}

export function AccountTab({ state }: { state: SettingsState }) {
  const user = state.account.user
  if (!user) return null
  const since = joined(user.since)
  return (
    <div>
      <Header eyebrow="Account" title="Your Plip account" />
      <Card className="mb-7 flex items-center gap-4 p-5">
        <Avatar user={user} size={52} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-[17px] font-semibold tracking-tight">{user.name || user.email}</div>
          {user.name && <div className="truncate text-[13px] text-white/50">{user.email}</div>}
        </div>
        <Pill tone="good">Signed in</Pill>
      </Card>

      <Section title="Sign-in">
        <Rows>
          <Row title="Google" detail={`You sign in to Plip with your Google account${since ? `. Member since ${since}` : ''}.`} action={<GoogleMark />} />
          <Row
            title="Sign out"
            detail="Plip stops working on this Mac until you sign in again. Your memory, history and settings stay."
            action={<Button variant="ghost" onClick={() => send('account-sign-out')}>Sign out</Button>}
          />
        </Rows>
      </Section>

      <Section title="What your account holds">
        <Rows>
          <Row title="Your name and email" detail="From Google, so we know who uses Plip. No picture, no contacts, nothing else." />
          <Row
            title="Not what you do with Plip"
            detail="What you ask, your screen, memory and history stay on this Mac and are never tied to your account."
          />
        </Rows>
      </Section>
    </div>
  )
}
