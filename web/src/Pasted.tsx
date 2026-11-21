/** Pictures pasted into a chat box (DESIGN.md 62).
 *
 *  Paste (Ctrl/⌘+V) or drop a picture onto the box and it waits above the
 *  text until the question is sent. It is shrunk here first - a phone photo
 *  is megabytes, and the model sees at most 1024 pixels a side anyway - and a
 *  small copy is kept to show in the conversation. The pasted picture goes to
 *  this computer's model only; it is not put on the board.
 */
import { useCallback, useState } from 'react'

import { Icon } from './icons'

export interface PastedPicture {
  /** what is sent: a JPEG, at most SEND_SIDE pixels a side */
  url: string
  /** what is shown in the conversation and kept in the tab */
  thumb: string
}

export const PASTED_MAX = 4
const SEND_SIDE = 1600
const THUMB_SIDE = 180

async function shrink(file: Blob, side: number, quality: number): Promise<string> {
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, side / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(bitmap.width * scale))
  canvas.height = Math.max(1, Math.round(bitmap.height * scale))
  const g = canvas.getContext('2d')!
  g.fillStyle = '#fff'                     // a transparent PNG on white, as it is usually seen
  g.fillRect(0, 0, canvas.width, canvas.height)
  g.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  return canvas.toDataURL('image/jpeg', quality)
}

async function read(file: Blob): Promise<PastedPicture | null> {
  try {
    return { url: await shrink(file, SEND_SIDE, 0.9), thumb: await shrink(file, THUMB_SIDE, 0.75) }
  } catch {
    return null                            // not a picture the browser can open
  }
}

const picturesIn = (list: DataTransferItemList | FileList | null | undefined): File[] => {
  if (!list) return []
  const out: File[] = []
  for (const item of Array.from(list as ArrayLike<DataTransferItem | File>)) {
    const file = item instanceof File ? item : item.kind === 'file' ? item.getAsFile() : null
    if (file && file.type.startsWith('image/')) out.push(file)
  }
  return out
}

/** the pictures waiting in a chat box, and the handlers that add them */
export function usePasted() {
  const [pictures, setPictures] = useState<PastedPicture[]>([])
  const [reading, setReading] = useState(0)

  const add = useCallback(async (files: File[]) => {
    if (!files.length) return
    setReading((n) => n + files.length)
    const got = (await Promise.all(files.slice(0, PASTED_MAX).map(read))).filter(Boolean) as PastedPicture[]
    setReading((n) => n - files.length)
    setPictures((had) => [...had, ...got].slice(0, PASTED_MAX))
  }, [])

  /** for the text box: a pasted picture is taken; pasted text is left to the box */
  const onPaste = useCallback((e: React.ClipboardEvent) => {
    const files = picturesIn(e.clipboardData?.items)
    if (!files.length) return
    e.preventDefault()
    void add(files)
  }, [add])

  const onDragOver = useCallback((e: React.DragEvent) => {
    if (Array.from(e.dataTransfer?.types ?? []).includes('Files')) { e.preventDefault(); e.stopPropagation() }
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    const files = picturesIn(e.dataTransfer?.files)
    if (!files.length) return
    e.preventDefault()
    e.stopPropagation()                    // not onto the board underneath
    void add(files)
  }, [add])

  const remove = (i: number) => setPictures((had) => had.filter((_, j) => j !== i))
  const clear = () => setPictures([])

  return { pictures, reading, add, onPaste, onDragOver, onDrop, remove, clear }
}

/** the pictures waiting to be sent, above the text */
export function PastedStrip({ pictures, reading, remove, className = '' }: {
  pictures: PastedPicture[]; reading: number; remove: (i: number) => void; className?: string
}) {
  if (!pictures.length && !reading) return null
  return (
    <div className={`pasted-strip ${className}`} onClick={(e) => e.stopPropagation()}>
      {pictures.map((p, i) => (
        <div key={i} className="pasted-pic">
          <img src={p.thumb} alt={`Pasted picture ${i + 1}`} />
          <button className="pasted-x" title="Take this picture out" aria-label="Take this picture out"
            onClick={() => remove(i)}><Icon name="x" size={10} /></button>
        </div>
      ))}
      {reading ? <div className="pasted-pic reading" aria-label="Reading the picture" /> : null}
      <span className="pasted-note">Sent with your question, to the model on this computer only</span>
    </div>
  )
}

/** the pictures a question was asked with, in the conversation */
export function AskedWith({ thumbs }: { thumbs?: string[] }) {
  if (!thumbs?.length) return null
  return (
    <div className="asked-with">
      {thumbs.map((t, i) => <img key={i} src={t} alt={`Pasted picture ${i + 1}`} />)}
    </div>
  )
}

/** a question with no words but a picture still asks something */
export const PICTURE_ONLY = 'Explain this picture.'

/** what a question with pictures was, for the conversation the model reads next time */
export function withSaid(text: string, said?: string[]): string {
  const got = (said ?? []).filter(Boolean)
  if (!got.length) return text
  return `${text}\n\n(Pasted ${got.length === 1 ? 'a picture' : `${got.length} pictures`} with this question: ${got.join(' / ')})`
}

/** a picture chosen from the computer, for whoever would rather not paste */
export function PickPicture({ add, className = '', size = 13 }: {
  add: (files: File[]) => void; className?: string; size?: number
}) {
  return (
    <label className={`pick-picture ${className}`} title="Add a picture to the question (or paste one)"
      onClick={(e) => e.stopPropagation()}>
      <input type="file" accept="image/*" multiple hidden
        onChange={(e) => { add(picturesIn(e.target.files)); e.target.value = '' }} />
      <Icon name="image" size={size} />
    </label>
  )
}
