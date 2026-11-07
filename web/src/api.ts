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

export const api = {
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
  ask: async (boardId: string, body: {
    question: string; cards: string[]; history: { role: string; text: string }[]; think: boolean
  }, onEvent: (name: string, data: any) => void, signal: AbortSignal): Promise<void> => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['X-Mindboard-Token'] = token
    const res = await fetch(`/api/boards/${boardId}/ask`, {
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
  },
  /** Stop the answer being written. Said out loud rather than left to the
   *  hang-up: the server does not always hear a connection close. */
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
  exportUrl: (boardId: string) =>
    `/api/boards/${boardId}/export${token ? `?t=${encodeURIComponent(token)}` : ''}`,
  backupUrl: () => `/api/backup${token ? `?t=${encodeURIComponent(token)}` : ''}`,
}
