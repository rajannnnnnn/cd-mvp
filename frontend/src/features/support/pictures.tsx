import { useEffect, useMemo, useRef, useState } from 'react'
import { ImagePlus, X } from 'lucide-react'
import { api, ok } from '@/api/client'
import { Modal } from '@/ui'
import { tr } from '@/i18n/tr'

/** Pictures on support tickets: PNG, JPEG or WebP, up to 3 MB each and 3 per message (the server checks the same limits). */
export const MAX_PICTURES = 3
export const MAX_BYTES = 3 * 1024 * 1024
const TYPES = ['image/png', 'image/jpeg', 'image/webp']

export type PictureRef = { id: string; content_type: string; size_bytes: number }

/** Sends the chosen pictures to a message that was just created. */
export async function uploadPictures(ticketId: string, messageId: number, files: File[]) {
  for (const file of files) {
    const form = new FormData(); form.append('file', file)
    await ok(api.POST('/api/v1/support/tickets/{ticket_id}/messages/{message_id}/attachments', { params: { path: { ticket_id: ticketId, message_id: messageId } }, body: form as never }))
  }
}

/** A picture that was already sent. It is fetched with the sign-in, so it never has a public address. Tap to see it large. */
export function PictureThumb({ id, load }: { id: string; load?: (id: string) => Promise<Blob | undefined> }) {
  const [url, setUrl] = useState<string | null>(null)
  const [big, setBig] = useState(false)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    let made: string | undefined; let gone = false
    // the admin console passes its own loader (its own sign-in); the owner app reads its own shop's picture
    const get = load ? load(id) : api.GET('/api/v1/support/attachments/{attachment_id}', { params: { path: { attachment_id: id } }, parseAs: 'blob' }).then((r) => r.data as Blob | undefined)
    get.then((blob) => { if (gone) return; if (blob) { made = URL.createObjectURL(blob); setUrl(made) } else setFailed(true) }).catch(() => setFailed(true))
    return () => { gone = true; if (made) URL.revokeObjectURL(made) }
  }, [id, load])
  if (failed) return <span className="text-xs text-muted">{tr('Picture unavailable')}</span>
  if (!url) return <span className="skeleton block h-24 w-24 !rounded-xl" aria-hidden />
  return (
    <>
      <button type="button" onClick={() => setBig(true)} className="block overflow-hidden rounded-xl border border-line/70" aria-label={tr('View picture')}><img src={url} alt={tr('Attached picture')} className="h-24 w-24 object-cover" /></button>
      <Modal open={big} onClose={() => setBig(false)} title={tr('Picture')} wide><img src={url} alt={tr('Attached picture')} className="mx-auto max-h-[70vh] w-auto max-w-full rounded-xl object-contain" /></Modal>
    </>
  )
}

/** "Add a picture (optional)": choose up to three, see them, remove any. Nothing is sent until the message is. */
export function PicturePicker({ files, onChange }: { files: File[]; onChange: (f: File[]) => void }) {
  const input = useRef<HTMLInputElement>(null)
  const [err, setErr] = useState<string | null>(null)
  const previews = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files])
  useEffect(() => () => previews.forEach((u) => URL.revokeObjectURL(u)), [previews])
  const add = (list: FileList | null) => {
    setErr(null)
    const next = [...files]
    for (const f of Array.from(list ?? [])) {
      if (!TYPES.includes(f.type)) { setErr(tr('Please choose a PNG, JPEG or WebP picture.')); continue }
      if (f.size > MAX_BYTES) { setErr(tr('“{name}” is over 3 MB. Please choose a smaller picture.', { name: f.name })); continue }
      if (next.length >= MAX_PICTURES) { setErr(tr('You can add up to {n} pictures.', { n: MAX_PICTURES })); break }
      next.push(f)
    }
    onChange(next)
    if (input.current) input.current.value = ''
  }
  return (
    <div className="space-y-2">
      <input ref={input} type="file" accept="image/png,image/jpeg,image/webp" multiple className="hidden" onChange={(e) => add(e.target.files)} />
      <div className="flex flex-wrap items-center gap-2">
        {previews.map((u, i) => (
          <span key={u} className="relative">
            <img src={u} alt={tr('Picture {n}', { n: i + 1 })} className="h-16 w-16 rounded-xl border border-line/70 object-cover" />
            <button type="button" onClick={() => onChange(files.filter((_, j) => j !== i))} aria-label={tr('Remove picture {n}', { n: i + 1 })}
              className="absolute -right-2 -top-2 grid h-6 w-6 place-items-center rounded-full border border-line bg-surface text-muted shadow-card hover:text-ink"><X className="h-3.5 w-3.5" /></button>
          </span>))}
        {files.length < MAX_PICTURES && <button type="button" className="btn btn-outline btn-sm" onClick={() => input.current?.click()}><ImagePlus className="h-4 w-4" /> {tr('Add a picture')} <span className="font-normal text-muted">({tr('optional')})</span></button>}
      </div>
      {err && <p role="alert" className="text-[13px] font-medium text-danger">{err}</p>}
    </div>
  )
}
