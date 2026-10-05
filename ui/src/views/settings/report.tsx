import { AnimatePresence, motion } from 'motion/react'
import { Bug, Check, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { send, settings, type SettingsState } from '../../bridge'
import { Button, Input } from './ui'

export function ReportIssue({ state, open, onClose }: { state: SettingsState; open: boolean; onClose: () => void }) {
  const [message, setMessage] = useState('')
  const [contact, setContact] = useState('')

  useEffect(() => {
    if (open) settings.set({ report: '' })
  }, [open])

  const sent = state.report === 'sent'
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="absolute inset-0 z-50 grid place-items-center bg-black/60 backdrop-blur-sm"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          onClick={onClose}
        >
          <motion.div
            className="card w-[460px] p-5"
            initial={{ y: 12, scale: 0.98 }} animate={{ y: 0, scale: 1 }} exit={{ y: 8, opacity: 0 }}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between">
              <div className="flex items-center gap-2 text-[15px] font-semibold"><Bug className="size-4 text-coral" /> Report an issue</div>
              <button onClick={onClose} className="text-white/40 hover:text-white"><X className="size-4" /></button>
            </div>
            {sent ? (
              <div className="py-6 text-center">
                <div className="mx-auto mb-3 grid size-10 place-items-center rounded-full bg-mint/15 text-emerald-300"><Check className="size-5" strokeWidth={3} /></div>
                <div className="text-[14px] font-semibold">Thanks! We got it.</div>
                <div className="mt-1 text-[12px] text-white/45">We read every report.</div>
                <Button className="mt-4" variant="ghost" onClick={() => { setMessage(''); onClose() }}>Close</Button>
              </div>
            ) : (
              <>
                <div className="mb-2 text-[12px] text-white/45">What went wrong? What did you ask, and what did Plip do?</div>
                <textarea
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder="I asked Plip to… and it…"
                  className="h-32 w-full resize-none rounded-xl bg-black/40 p-3 text-[13px] leading-relaxed text-white outline-none hairline placeholder:text-white/25"
                />
                <Input className="mt-2" value={contact} onChange={setContact} placeholder="Email (optional, if you'd like a reply)" />
                <div className="mt-2 text-[11px] text-white/30">Sends your message, app version, macOS version and which AI you use. No screenshots or files.</div>
                {state.report === 'failed' && <div className="mt-2 text-[12px] text-coral">Couldn't send. Check your internet and try again.</div>}
                <div className="mt-4 flex justify-end gap-2">
                  <Button variant="quiet" onClick={onClose}>Cancel</Button>
                  <Button variant="brand" disabled={!message.trim()} onClick={() => send('report-issue', { message, contact })}>Send report</Button>
                </div>
              </>
            )}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
