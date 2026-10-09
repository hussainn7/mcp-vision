import { AnimatePresence, motion } from 'motion/react'
import { LoaderCircle, Lock } from 'lucide-react'
import { useState } from 'react'
import { send, type AccountState, type SettingsState } from '../../bridge'
import { Mascot } from '../../components/Mascot'
import { cn } from '../../components/bits'
import { PRIVACY_URL } from './onboarding'
import { Button, Card, Header, Pill, Row, Rows, Section } from './ui'

type User = NonNullable<AccountState['user']>

/** Why sign in at all: the one reason Plip gives, everywhere it asks. */
export const WHY_SIGN_IN = 'Pro is coming, and signed-in people get it first. Google only, one click, nothing else shared.'

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

/** Their Google picture (kept on this Mac), or their initials in a circle. A guest ("Quiet Nomad") gets a quieter one. */
export function Avatar({ user, size = 20, className }: { user: User; size?: number; className?: string }) {
  const [broken, setBroken] = useState('')
  const guest = !user.email
  const words = (user.name || user.email || '?').trim().split(/\s+/)
  const initial = (words.length > 1 ? words[0].charAt(0) + words[1].charAt(0) : words[0].charAt(0)).toUpperCase()
  if (user.picture && user.picture !== broken)
    return (
      <img
        src={user.picture}
        alt=""
        draggable={false}
        onError={() => setBroken(user.picture ?? '')}
        className={cn('shrink-0 rounded-full object-cover ring-1 ring-white/10', className)}
        style={{ width: size, height: size }}
      />
    )
  return (
    <span
      className={cn('grid shrink-0 place-items-center rounded-full font-semibold text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.35)]',
        guest ? 'bg-gradient-to-br from-white/25 to-white/10 text-white/80' : 'bg-gradient-to-br from-plip-300 to-plip-600', className)}
      style={{ width: size, height: size, fontSize: Math.round(size * (initial.length > 1 ? 0.36 : 0.46)) }}
    >
      {initial}
    </span>
  )
}

/** Continue with Google, and what's happening while the browser is out. Large and centered, or compact in a card. */
export function GoogleSignIn({ state, className, size = 'lg' }: { state: SettingsState; className?: string; size?: 'md' | 'lg' }) {
  const { status, error } = state.account
  const waiting = status === 'waiting'
  const failed = status === 'failed'
  return (
    <div className={cn('flex flex-col', size === 'lg' ? 'items-center' : 'items-start', className)}>
      <button
        onClick={() => send('account-sign-in', { provider: 'google' })}
        disabled={waiting}
        className={cn(
          'inline-flex items-center justify-center gap-3 rounded-full bg-white font-semibold tracking-tight text-slate-950 transition',
          'shadow-[inset_0_-2px_0_rgba(0,0,0,0.08),0_10px_30px_-10px_rgba(156,194,250,0.55)] hover:bg-plip-50 active:scale-[0.98] disabled:cursor-default disabled:opacity-70 disabled:active:scale-100',
          size === 'lg' ? 'h-12 w-full max-w-[320px] px-6 text-[15px]' : 'h-10 px-5 text-[13.5px]',
        )}
      >
        {waiting ? <LoaderCircle className="size-5 animate-spin text-plip-500" /> : <GoogleMark />}
        {waiting ? 'Waiting for Google…' : failed ? 'Try again with Google' : 'Continue with Google'}
      </button>

      <div className={cn('mt-4 min-h-[44px]', size === 'lg' && 'text-center')}>
        <AnimatePresence mode="wait">
          {waiting && (
            <motion.div key="waiting" initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="text-[12.5px] text-white/45">
              Finish signing in in your browser, then come back here.
              <div className={cn('mt-1.5 flex items-center gap-1', size === 'lg' && 'justify-center')}>
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
    </div>
  )
}

/** The card Home shows after the first task (and the third, and when an update is out): one button, one Later. */
export function SignInCard({ state }: { state: SettingsState }) {
  const account = state.account
  const name = account.user?.name || 'you'
  return (
    <Card className="mb-6 flex items-start gap-4 glow-ring">
      <div className="relative mt-0.5 shrink-0">
        <div className="absolute inset-0 -z-10 scale-[1.6] rounded-full bg-plip-400/25 blur-2xl" />
        <Mascot size={52} mood={account.status === 'failed' ? 'error' : account.status === 'waiting' ? 'thinking' : 'happy'} />
      </div>
      <div className="min-w-0 flex-1">
        <div className="text-[15px] font-semibold tracking-tight">Make this Plip yours</div>
        <div className="mt-1 text-[12.5px] leading-relaxed text-white/50">
          You’re <span className="font-medium text-white/75">{name}</span> for now, and Plip works fully like this. {WHY_SIGN_IN}
        </div>
        <div className="mt-4 flex items-start gap-3">
          <GoogleSignIn state={state} size="md" />
          {account.status !== 'waiting' && (
            <Button variant="quiet" className="h-10" onClick={() => send('account-later')}>Later</Button>
          )}
        </div>
      </div>
    </Card>
  )
}

function joined(since: number | null): string {
  return since ? new Date(since * 1000).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' }) : ''
}

export function AccountTab({ state }: { state: SettingsState }) {
  const account = state.account
  const user = account.user
  if (!account.available) return null
  const since = joined(user?.since ?? null)
  const signedIn = Boolean(user?.email)
  return (
    <div>
      <Header eyebrow="Account" title={signedIn ? 'Your Plip account' : 'Your Plip, so far'} />
      <Card className="mb-7 flex items-center gap-4 p-5">
        {user ? <Avatar user={user} size={52} /> : <Mascot size={52} mood="idle" />}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[17px] font-semibold tracking-tight">
            {user ? (user.name || user.email) : 'Setting up your account…'}
          </div>
          {signedIn && user?.name && <div className="truncate text-[13px] text-white/50">{user.email}</div>}
          {!signedIn && user && <div className="text-[13px] text-white/50">Guest{since ? ` · since ${since}` : ''}. Plip works fully like this.</div>}
        </div>
        <Pill tone={signedIn ? 'good' : 'muted'}>{signedIn ? 'Signed in' : 'Guest'}</Pill>
      </Card>

      {!signedIn && (
        <Card className="mb-7 p-5">
          <div className="flex items-start gap-3">
            <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-white shadow-[0_6px_20px_-8px_rgba(156,194,250,0.6)]"><GoogleMark className="size-[18px]" /></span>
            <div className="min-w-0 flex-1">
              <div className="text-[14px] font-semibold tracking-tight">Sign in with Google</div>
              <div className="mt-1 text-[12.5px] leading-relaxed text-white/50">{WHY_SIGN_IN} It links to this account, so what you’ve done stays yours.</div>
              <div className="mt-4"><GoogleSignIn state={state} size="md" /></div>
            </div>
          </div>
        </Card>
      )}

      {signedIn && (
        <Section title="Sign-in">
          <Rows>
            <Row title="Google" detail={`You sign in to Plip with your Google account${since ? `. Member since ${since}` : ''}.`} action={<GoogleMark />} />
            <Row
              title="Sign out"
              detail="Plip keeps working on this Mac as a guest. Your memory, history and settings stay."
              action={<Button variant="ghost" onClick={() => send('account-sign-out')}>Sign out</Button>}
            />
          </Rows>
        </Section>
      )}

      <Section title="What your account holds">
        <Rows>
          <Row title={signedIn ? 'Your name, email and picture' : 'A name like Quiet Nomad'}
            detail={signedIn ? 'From Google, so we know who uses Plip. No contacts, nothing else.'
              : 'Picked at random here, so you count as one person using Plip. Google adds your real name, email and picture when you sign in.'} />
          <Row
            title="Not what you do with Plip"
            detail="What you ask, your screen, memory and history stay on this Mac and are never tied to your account. Only which setup steps you reached are counted."
          />
        </Rows>
      </Section>
      <div className="mt-6 flex items-center gap-1.5 text-[11.5px] text-white/35">
        <Lock className="size-3.5 shrink-0 text-mint" />
        <button className="text-white/45 underline-offset-2 hover:text-white/75 hover:underline" onClick={() => send('open-url', { url: PRIVACY_URL })}>Privacy policy</button>
      </div>
    </div>
  )
}
