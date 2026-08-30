import type { BoardMeta, Envelope, MEdge, MNode } from './types'

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
  fromUrl: (boardId: string, url: string) =>
    req<any>(`/boards/${boardId}/assets/from-url`, { method: 'POST', body: JSON.stringify({ url }) }),
  importBundle: async (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return req<BoardMeta>('/boards/import', { method: 'POST', body: fd })
  },

  assetUrl: (boardId: string, sha: string) =>
    `/api/boards/${boardId}/assets/${sha}${token ? `?t=${encodeURIComponent(token)}` : ''}`,
  exportUrl: (boardId: string) =>
    `/api/boards/${boardId}/export${token ? `?t=${encodeURIComponent(token)}` : ''}`,
}
