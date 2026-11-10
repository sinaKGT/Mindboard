import type { BoardMeta, Envelope, MEdge, MNode } from './types'

export type ImportResult =
  | ({ kind: 'board' } & BoardMeta)
  | {
      kind: 'backup'
      restored: BoardMeta[]
      skipped: { id: string; title: string }[]
      failed: { title: string; error: string }[]
      backup_created_at?: string
    }

const params = new URLSearchParams(location.search)
const fromUrl = params.get('t')
if (fromUrl) {
  try { localStorage.setItem('mb_token', fromUrl) } catch { /* private mode */ }
  history.replaceState({}, '', location.pathname + location.hash)
}
let token = fromUrl || ''
if (!token) { try { token = localStorage.getItem('mb_token') || '' } catch { token = '' } }

/** a theme of your own: a name, the built-in theme it starts from, its colours */
export interface CustomTheme { id: string; name: string; base: string; palette: Record<string, string> }

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as any) }
  if (token) headers['X-Mindboard-Token'] = token
  if (init.body && typeof init.body === 'string') headers['Content-Type'] = 'application/json'
  const res = await fetch(`/api${path}`, { ...init, headers, credentials: 'same-origin' })
  if (!res.ok) {
    let detail = res.statusText
    try { detail = (await res.json()).detail ?? detail } catch { /* non-json */ }
    throw new Error(detail)
  }
  return res.status === 204 ? (undefined as T) : res.json()
}

const apiCore = {
  token: () => token,
  listBoards: () => req<{ boards: BoardMeta[] }>('/boards').then((r) => r.boards),
  createBoard: (title: string, description = '') =>
    req<BoardMeta>('/boards', { method: 'POST', body: JSON.stringify({ title, description }) }),
  patchBoard: (id: string, patch: Partial<Pick<BoardMeta, 'title' | 'description' | 'settings'>>) =>
    req<BoardMeta>(`/boards/${id}`, { method: 'PATCH', body: JSON.stringify(patch) }),
  deleteBoard: (id: string) => req<{ deleted: boolean }>(`/boards/${id}`, { method: 'DELETE' }),

  graph: (id: string) =>
    req<{ board: BoardMeta; revision: number; nodes: MNode[]; edges: MEdge[] }>(`/boards/${id}/graph`),
  ops: (id: string, envelopes: Envelope[], baseRevision?: number) =>
    req<{ revision: number; applied: string[]; rejected: { opId: string; error: string }[] }>(
      `/boards/${id}/ops`, { method: 'POST', body: JSON.stringify({ baseRevision, envelopes }) }),
  search: (id: string, q: string) =>
    req<{ results: { id: string; type: string; title: string; parent: string | null; snippet: string }[] }>(
      `/boards/${id}/search?q=${encodeURIComponent(q)}`),

  upload: async (boardId: string, file: File, extra: Record<string, string | number> = {}) => {
    const fd = new FormData()
    fd.append('file', file)
    for (const [k, v] of Object.entries(extra)) fd.append(k, String(v))
    return req<{ sha256: string; mime: string; width: number | null; height: number | null }>(
      `/boards/${boardId}/assets`, { method: 'POST', body: fd })
  },
  /** Media is stored locally; a page comes back with a stored preview and,
   *  when it is a video we can play, its embed address. */
  fromUrl: (boardId: string, url: string) =>
    req<any>(`/boards/${boardId}/assets/from-url`, { method: 'POST', body: JSON.stringify({ url }) }),
  /** Give this board its own copy of a file another board holds - what a node
   *  pasted across boards needs, since each board owns its media outright. */
  copyAsset: (boardId: string, fromBoard: string, sha: string) =>
    req<{ sha256: string; mime: string }>(`/boards/${boardId}/assets/copy`, {
      method: 'POST', body: JSON.stringify({ from_board: fromBoard, sha256: sha }),
    }),
  /** Open one of the board's files in this computer's own app for it - a PDF
   *  in your PDF reader. The server hands over a read-only copy; see app.py. */
  openAsset: (boardId: string, sha: string) =>
    req<{ opened: string }>(`/boards/${boardId}/assets/${sha}/open`, { method: 'POST' }),
  /** Ask the local model about exactly these cards. Calls `onEvent` for each
   *  server-sent event (context, thinking, text, done, error) as it arrives;
   *  aborting `signal` stops the model. Throws with the server's reason when
   *  the question cannot be asked at all. */
  ask: (boardId: string, body: {
    question: string; cards: string[]; history: { role: string; text: string }[]; think: boolean
    keep: string[]; explain: boolean
  }, onEvent: (name: string, data: any) => void, signal: AbortSignal): Promise<void> =>
    streamEvents(`/boards/${boardId}/ask`, body, onEvent, signal),
  /** Ask one expert: its boards searched together, answered in its voice. */
  askExpert: (expertId: string, body: { question: string; history: { role: string; text: string }[] },
              onEvent: (name: string, data: any) => void, signal: AbortSignal): Promise<void> =>
    streamEvents(`/experts/${expertId}/ask`, { ...body, cards: [], think: false }, onEvent, signal),
  experts: () => req<{ experts: Expert[]; shapes: ShapeState[] }>('/experts'),
  createExpert: (body: Partial<ExpertDraft>) =>
    req<Expert>('/experts', { method: 'POST', body: JSON.stringify(body) }),
  updateExpert: (id: string, body: Partial<ExpertDraft>) =>
    req<Expert>(`/experts/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  deleteExpert: (id: string) => req<{ deleted: boolean }>(`/experts/${id}`, { method: 'DELETE' }),
  /** your own themes, offered on every board (DESIGN.md 52) */
  themes: () => req<{ themes: CustomTheme[] }>('/themes'),
  saveTheme: (body: { name: string; base: string; palette: Record<string, string> }) =>
    req<CustomTheme>('/themes', { method: 'POST', body: JSON.stringify(body) }),
  deleteTheme: (id: string) => req<{ deleted: boolean }>(`/themes/${id}`, { method: 'DELETE' }),
  /** is the experts' voice set up, and which voices there are */
  voice: () => req<{ installed: boolean; cast: string[]; listen: boolean; turn?: boolean; talk?: boolean }>('/voice'),
  /** words from a short recording, made on this computer (whisper.cpp) */
  listen: async (wav: Blob): Promise<string> => {
    const headers: Record<string, string> = { 'Content-Type': 'audio/wav' }
    if (token) headers['X-Mindboard-Token'] = token
    const res = await fetch('/api/voice/listen', { method: 'POST', headers, body: wav, credentials: 'same-origin' })
    if (!res.ok) throw new Error('could not make out the words')
    return (await res.json()).text as string
  },
  /** have you finished speaking? Smart Turn reads the sound of the turn so far (DESIGN.md 44) */
  turn: async (wav: Blob): Promise<{ complete: boolean; probability: number }> => {
    const headers: Record<string, string> = { 'Content-Type': 'audio/wav' }
    if (token) headers['X-Mindboard-Token'] = token
    const res = await fetch('/api/voice/turn', { method: 'POST', headers, body: wav, credentials: 'same-origin' })
    if (!res.ok) throw new Error('could not tell')
    return res.json()
  },
  /** stop an expert's answer - Stop, or speaking over it */
  expertStop: () => req<{ stopped: boolean }>('/experts/ask/stop', { method: 'POST' }),
  /** one sentence, spoken on this computer, as WAV bytes */
  speak: async (text: string, voice: string, speed = 1): Promise<ArrayBuffer> => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['X-Mindboard-Token'] = token
    const res = await fetch('/api/voice/speak', { method: 'POST', headers, credentials: 'same-origin',
      body: JSON.stringify({ text, voice, speed }) })
    if (!res.ok) throw new Error('could not speak')
    return res.arrayBuffer()
  },
  /** Who can help with this question: the experts whose boards answer it best */
  guide: (question: string) =>
    req<{ picked: { id: string; name: string; score: number; cards: string[] }[]; why: string }>(
      '/experts/guide', { method: 'POST', body: JSON.stringify({ question }) }),
}

/** a monument, and whether the library is big enough for it yet */
export interface ShapeState { shape: string; unlocked: boolean; need: string; have: number; of: number }
export interface ExpertDraft { name: string; brief: string; boards: string[]; colour: string; shape: string; voice?: string }
export interface Expert extends Omit<ExpertDraft, 'boards'> {
  id: string; cards: number; boards: { id: string; title: string; cards: number }[]
}

/** Server-sent events from a POST: each event as it arrives. Aborting `signal`
 *  stops the model. Throws with the server's reason when it refuses. */
async function streamEvents(path: string, body: unknown, onEvent: (name: string, data: any) => void,
                            signal: AbortSignal): Promise<void> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['X-Mindboard-Token'] = token
    const res = await fetch(`/api${path}`, {
      method: 'POST', headers, body: JSON.stringify(body), credentials: 'same-origin', signal,
    })
    if (!res.ok || !res.body) {
      let detail = res.statusText
      try { detail = (await res.json()).detail ?? detail } catch { /* not json */ }
      throw new Error(detail)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let cut: number
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, cut)
        buffer = buffer.slice(cut + 2)
        let name = 'message'
        let data = ''
        for (const line of block.split('\n')) {
          if (line.startsWith('event: ')) name = line.slice(7)
          else if (line.startsWith('data: ')) data += line.slice(6)
        }
        if (data) onEvent(name, JSON.parse(data))
      }
    }
}

const apiMore = {
  /** A card drafted from an answer, to check before adding (nothing is written) */
  /** a card drafted from an answer: the new knowledge, where it belongs and how (DESIGN.md 53) */
  askDraft: (boardId: string, body: { question: string; answer: string; cited: string[];
                                      candidates?: string[]; history?: { role: string; text: string }[] }) =>
    req<{ title: string; text: string; link_to: string; kind: string; label: string; why: string }>(
      `/boards/${boardId}/ask/draft`, { method: 'POST', body: JSON.stringify(body) }),
  askStop: (boardId: string) =>
    req<{ stopped: boolean }>(`/boards/${boardId}/ask/stop`, { method: 'POST' }),
  /** Accepts a single .mindboard bundle or a whole backup zip. */
  importBundle: async (file: File, mode: 'skip' | 'replace' | 'copy' = 'skip') => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('mode', mode)
    return req<ImportResult>('/boards/import', { method: 'POST', body: fd })
  },

  assetUrl: (boardId: string, sha: string) =>
    `/api/boards/${boardId}/assets/${sha}${token ? `?t=${encodeURIComponent(token)}` : ''}`,
  /** a picture, small: made once on the server and kept (assets.thumbnail) */
  thumbUrl: (boardId: string, sha: string, size = 240) =>
    `/api/boards/${boardId}/assets/${sha}/thumb?size=${size}${token ? `&t=${encodeURIComponent(token)}` : ''}`,
  exportUrl: (boardId: string) =>
    `/api/boards/${boardId}/export${token ? `?t=${encodeURIComponent(token)}` : ''}`,
  backupUrl: () => `/api/backup${token ? `?t=${encodeURIComponent(token)}` : ''}`,
  /** what a backup made now would lack: files on the boards found nowhere (DESIGN.md 49) */
  backupCheck: () => req<{ files: number; recovered: number;
    missing: { sha: string; board: string; board_title: string; cards: string[] }[] }>('/backup/check'),
}

/** Everything the interface asks the server, in one place. */
export const api = { ...apiCore, ...apiMore }
