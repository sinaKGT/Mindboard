import { Handle, NodeResizer, Position, type NodeProps } from '@xyflow/react'
import { useEffect, useMemo, useRef, useState } from 'react'

import {
  addConnected, deleteNodes, linkifyNode, setBodyList, setStyle, unwrapMeta, updateNode,
} from './actions'
import { api } from './api'
import { Icon } from './icons'
import { GraphMini, toMini, type MiniEdge } from './Mini'
import { useStore } from './store'
import { blocks, continueList, detectList, URL_ONLY } from './text'
import { NODE_COLOURS } from './theme'
import type { MNode, NodeTone } from './types'
import { useUI } from './ui'

const PORTS: [string, Position][] = [
  ['t', Position.Top], ['r', Position.Right], ['b', Position.Bottom], ['l', Position.Left],
]

const TYPE_VAR: Record<string, string> = {
  text: 'var(--mb-type-text)', image: 'var(--mb-type-image)', video: 'var(--mb-type-video)',
  link: 'var(--mb-type-link)', file: 'var(--mb-type-file)', meta: 'var(--mb-type-meta)',
}

/** Under this much pointer travel, a press on a port was a click, not a drag.
 *  The canvas uses the same number - keep them in step. */
export const CLICK_SLOP = 6

const accentOf = (n: MNode) =>
  // a link to a video is a video, and reads better in the video colour
  n.style.color || (n.type === 'link' && n.content.embed ? TYPE_VAR.video : TYPE_VAR[n.type])
const sizeOf = (n: MNode) => n.style.fontSize ?? 13

/** Meta-nodes used to keep their description under `summary`; read both. */
const bodyText = (n: MNode): string => n.content.text ?? n.content.summary ?? ''

/** A port is both a drag handle and a button.
 *
 *  Dragging from it is xyflow's own connection gesture. A *click* - press and
 *  release without travelling - starts click-to-click linking instead. We
 *  detect that here rather than in onConnectEnd, because xyflow does not
 *  reliably report a connection that never moved. The canvas applies the same
 *  travel threshold so a click never also drops a node.
 */
function Ports({ nodeId }: { nodeId: string }) {
  const setUI = useUI((s) => s.set)
  const down = useRef<{ x: number; y: number } | null>(null)

  return (
    <>
      {PORTS.map(([id, position]) => (
        <Handle key={id} id={id} type="source" position={position}
          title="Drag to connect — or click, then click another node"
          onPointerDown={(e: React.PointerEvent) => {
            down.current = { x: e.clientX, y: e.clientY }
          }}
          onPointerUp={(e: React.PointerEvent) => {
            const start = down.current
            down.current = null
            if (!start) return
            if (Math.hypot(e.clientX - start.x, e.clientY - start.y) < CLICK_SLOP) {
              setUI('linking', { sourceId: nodeId })
            }
          }} />
      ))}
    </>
  )
}

// --------------------------------------------------------------- editing -- //

function useAutoHeight(value: string, enabled: boolean) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el || !enabled) return
    el.style.height = '0px'
    el.style.height = `${el.scrollHeight}px`
  }, [value, enabled])
  return ref
}

/** The editable title + body every node type shares, so an image or a video
 *  carries a thought the same way a text node does. */
function NodeText({ node, placeholder = 'Untitled', bodyPlaceholder = 'Add a note...' }: {
  node: MNode; placeholder?: string; bodyPlaceholder?: string
}) {
  const editing = useUI((s) => s.editingId === node.id)
  const setUI = useUI((s) => s.set)
  const [title, setTitle] = useState(node.title)
  const [text, setText] = useState<string>(bodyText(node))
  const area = useAutoHeight(text, editing)
  const titleRef = useRef<HTMLInputElement>(null)
  const idle = useRef<ReturnType<typeof setTimeout>>()
  /** Once a node is on its way to becoming a link, this edit is over. Without
   *  this the debounced auto-save fires after the conversion lands and writes
   *  the raw address back over the link it just became. */
  const converting = useRef(false)

  useEffect(() => {
    if (!editing) { setTitle(node.title); setText(bodyText(node)) }
  }, [node.title, node.content.text, node.content.summary, editing])

  useEffect(() => {
    if (editing) { converting.current = false; titleRef.current?.focus() }
  }, [editing])

  const commit = () => {
    clearTimeout(idle.current)
    if (converting.current) return
    // A text node whose whole body is a link is a link - fetch it rather than
    // leaving the address sitting there as prose.
    if (node.type === 'text' && URL_ONLY.test(text.trim())) {
      converting.current = true
      void linkifyNode(node.id, text.trim())
      return
    }
    if (title !== node.title || text !== bodyText(node)) {
      // meta-nodes used to store this under `summary`; migrate on first edit
      const { summary: _dropped, ...rest } = node.content as Record<string, unknown>
      updateNode(node.id, { title, content: { ...rest, text } })
    }
  }

  /** Pasting a bare link into an empty node makes the link, not its address. */
  const onPasteUrl = (e: React.ClipboardEvent) => {
    if (node.type !== 'text' || node.title || bodyText(node)) return
    const dropped = e.clipboardData.getData('text/plain').trim()
    if (!URL_ONLY.test(dropped)) return
    e.preventDefault()
    clearTimeout(idle.current)
    converting.current = true
    setUI('editingId', null)
    void linkifyNode(node.id, dropped)
  }
  const touch = () => { clearTimeout(idle.current); idle.current = setTimeout(commit, 1500) }
  const fs = sizeOf(node)

  if (editing) {
    return (
      <div className="body">
        <input ref={titleRef} className="title-input nodrag" value={title} placeholder={placeholder}
          style={{ fontSize: fs + 1 }}
          onPaste={onPasteUrl}
          onChange={(e) => { setTitle(e.target.value); touch() }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') { e.preventDefault(); area.current?.focus() }
            if (e.key === 'Escape') { commit(); setUI('editingId', null) }
          }} />
        <textarea ref={area} className="nodrag" value={text} rows={1} placeholder={bodyPlaceholder}
          style={{ fontSize: fs }}
          onPaste={onPasteUrl}
          onChange={(e) => { setText(e.target.value); touch() }}
          onKeyDown={(e) => {
            if (e.key === 'Escape' || (e.key === 'Enter' && (e.metaKey || e.ctrlKey))) {
              e.preventDefault(); commit(); setUI('editingId', null)
              return
            }
            if (e.key === 'Enter' && !e.shiftKey) {
              // carry the list on to the next line, the way any editor does
              const el = e.currentTarget
              const next = continueList(el.value, el.selectionStart ?? el.value.length)
              if (next) {
                e.preventDefault()
                // Put the text and the caret in place synchronously. Deferring
                // the caret to a frame later loses characters when someone
                // types quickly through the line break.
                el.value = next.value
                el.setSelectionRange(next.caret, next.caret)
                setText(next.value)
                touch()
              }
            }
          }}
          onBlur={() => { commit(); setUI('editingId', null) }} />
      </div>
    )
  }

  // double-click is handled once, by the canvas, so it works over the picture
  // in an image node as readily as over the caption
  const text_ = bodyText(node)
  return (
    <div className="body" style={{ textAlign: node.style.align }}>
      <div className="title" style={{ fontSize: fs + 1 }}>
        {node.title || <span className="placeholder">{placeholder}</span>}
      </div>
      {text_
        ? <div className="text" style={{ fontSize: fs }}>
            {blocks(text_).map((block, i) => (
              block.kind === 'p'
                ? <p key={i}>{block.items.join('\n')}</p>
                : block.kind === 'ul'
                  ? <ul key={i}>{block.items.map((it, j) => <li key={j}>{it}</li>)}</ul>
                  : <ol key={i}>{block.items.map((it, j) => <li key={j}>{it}</li>)}</ol>
            ))}
          </div>
        : <div className="text placeholder hint-on-hover" style={{ fontSize: fs }}>
            {bodyPlaceholder}
          </div>}
    </div>
  )
}

// ----------------------------------------------------------------- shell -- //

const TONES: { key: NodeTone; label: string }[] = [
  { key: 'accent', label: 'Accent bar' },
  { key: 'tinted', label: 'Tinted card' },
  { key: 'solid', label: 'Solid card' },
]

const SIZES = [11, 12, 13, 14, 16, 18, 20, 24, 28, 34]

function Tools({ node }: { node: MNode }) {
  const enter = useStore((s) => s.enter)
  const size = sizeOf(node)
  const [sizeOpen, setSizeOpen] = useState(false)
  const listKind = detectList(bodyText(node))
  const tone: NodeTone = node.style.tone ?? 'accent'
  return (
    <div className="node-tools nodrag nopan" onDoubleClick={(e) => e.stopPropagation()}
      onMouseLeave={() => setSizeOpen(false)}>
      {NODE_COLOURS.slice(0, 6).map((c) => (
        <button key={c} className="icon-btn" title="Colour" onClick={() => setStyle(node, { color: c })}>
          <span className="swatch" style={{ background: c }} />
        </button>
      ))}
      <button className="icon-btn" title="Default colour"
        onClick={() => setStyle(node, { color: undefined })}>
        <Icon name="x" size={14} />
      </button>
      <span className="sep" />
      <span className="size-group">
        <button className="icon-btn" title="Smaller text" aria-label="Smaller text"
          onClick={() => setStyle(node, { fontSize: Math.max(11, size - 1) })}>
          <span className="size-a small">A</span>
        </button>
        <button className="size-value" title="Pick a text size"
          onClick={() => setSizeOpen((v) => !v)}>{size}</button>
        <button className="icon-btn" title="Larger text" aria-label="Larger text"
          onClick={() => setStyle(node, { fontSize: Math.min(34, size + 1) })}>
          <span className="size-a big">A</span>
        </button>
        {sizeOpen && (
          <div className="size-menu">
            {SIZES.map((s) => (
              <button key={s} className={s === size ? 'on' : ''}
                onClick={() => { setStyle(node, { fontSize: s }); setSizeOpen(false) }}>
                <span className="sample" style={{ fontSize: Math.min(s, 19) }}>A</span>
                <span className="n">{s}</span>
              </button>
            ))}
          </div>
        )}
      </span>
      <span className="sep" />
      <span className="tone-group" title="How far the colour carries">
        {TONES.map((t) => (
          <button key={t.key} className={`tone-chip ${t.key} ${tone === t.key ? 'on' : ''}`}
            title={t.label} aria-label={t.label}
            style={{ ['--node-color' as string]: accentOf(node) }}
            onClick={() => setStyle(node, { tone: t.key })} />
        ))}
      </span>
      <span className="sep" />
      <button className={`icon-btn ${listKind === 'bullet' ? 'on' : ''}`} title="Bullet list"
        onClick={() => setBodyList(node, listKind === 'bullet' ? 'none' : 'bullet')}>
        <Icon name="bullets" size={15} />
      </button>
      <button className={`icon-btn ${listKind === 'number' ? 'on' : ''}`} title="Numbered list"
        onClick={() => setBodyList(node, listKind === 'number' ? 'none' : 'number')}>
        <Icon name="numbers" size={15} />
      </button>
      <span className="sep" />
      {node.type === 'meta' && (
        <>
          <button className="icon-btn" title="Open" onClick={() => enter(node.id)}>
            <Icon name="enter" size={15} />
          </button>
          <button className="icon-btn" title="Ungroup" onClick={() => unwrapMeta(node.id)}>
            <Icon name="layers" size={15} />
          </button>
        </>
      )}
      <button className="icon-btn" title="Add connected node" onClick={() => addConnected(node)}>
        <Icon name="branch" size={15} />
      </button>
      <button className="icon-btn danger" title="Delete" onClick={() => deleteNodes([node.id])}>
        <Icon name="trash" size={15} />
      </button>
    </div>
  )
}

function Shell({ node, selected, resizable, children }: {
  node: MNode; selected?: boolean; resizable?: boolean; children: React.ReactNode
}) {
  const sparked = useUI((s) => s.sparkIds.includes(node.id))
  return (
    <>
      {resizable && (
        <NodeResizer isVisible={!!selected} minWidth={150} minHeight={60}
          lineStyle={{ borderColor: 'var(--mb-accent)' }}
          handleStyle={{ width: 7, height: 7, borderRadius: 2, background: 'var(--mb-accent)' }}
          onResizeEnd={(_, p) => updateNode(node.id, { size: [p.width, p.height] })} />
      )}
      <Tools node={node} />
      <div className={`mb-node ${node.type} ${node.provenance === 'ai' ? 'ai' : ''}`
        + ` tone-${node.style.tone ?? 'accent'}${node.style.color ? ' has-colour' : ''}`
        + `${sparked ? ' spark' : ''}${node.size[1] ? ' sized-h' : ''}`}
        style={{
          ['--node-color' as string]: accentOf(node),
          width: node.size[0] ? '100%' : undefined,
          height: node.size[1] ? '100%' : undefined,
          // the default max-width keeps unsized nodes readable; once you have
          // resized one by hand, your size wins
          maxWidth: node.size[0] ? 'none' : undefined,
        }}>
        <div className="accent" style={{ background: accentOf(node) }} />
        {children}
      </div>
      <Ports nodeId={node.id} />
    </>
  )
}

// ----------------------------------------------------------------- types -- //

export function TextNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  return (
    <Shell node={node} selected={selected} resizable>
      <NodeText node={node} placeholder="Untitled" bodyPlaceholder="Add a note..." />
    </Shell>
  )
}

export function ImageNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  const sha = node.content.sha256
  return (
    <Shell node={node} selected={selected} resizable>
      {sha && boardId
        ? <img className="media" src={api.assetUrl(boardId, sha)} alt={node.content.alt ?? ''}
            draggable={false} />
        : null}
      <NodeText node={node} placeholder="Untitled image"
        bodyPlaceholder="What does this image show?" />
    </Shell>
  )
}

export function VideoNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  const sha = node.content.sha256
  const poster = node.content.poster
  const posterUrl = poster && boardId ? api.assetUrl(boardId, poster) : undefined
  return (
    <Shell node={node} selected={selected} resizable>
      {sha && boardId ? (
        <>
          <video className="media nodrag nopan" controls preload="metadata"
            poster={posterUrl} src={api.assetUrl(boardId, sha)}
            onDoubleClick={(e) => e.stopPropagation()} />
          {/* stands in for the video when the board is exported as an image */}
          {posterUrl ? <img className="media video-poster" src={posterUrl} alt="" /> : null}
        </>
      ) : null}
      <NodeText node={node} placeholder="Untitled video"
        bodyPlaceholder="What is worth noticing here?" />
      {node.content.duration_ms ? (
        <div className="meta-line duration">
          {Math.floor(node.content.duration_ms / 60000)}:
          {String(Math.floor((node.content.duration_ms % 60000) / 1000)).padStart(2, '0')}
        </div>
      ) : null}
    </Shell>
  )
}

export function LinkNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  const [playing, setPlaying] = useState(false)
  const thumb = node.content.thumb && boardId
    ? api.assetUrl(boardId, node.content.thumb) : undefined
  const embed = node.content.embed as string | undefined

  return (
    <Shell node={node} selected={selected} resizable>
      {(thumb || embed) && (
        <div className="media-frame">
          {thumb
            ? <img className="media" src={thumb} alt="" draggable={false} />
            : <div className="media no-preview" />}
          {/* nothing is requested from the internet until you press play */}
          {embed && !playing && (
            <button className="play nodrag nopan" title="Play here"
              onDoubleClick={(e) => e.stopPropagation()}
              onClick={(e) => { e.stopPropagation(); setPlaying(true) }}>
              <svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
                <path d="M8 5.2v13.6L19 12z" fill="currentColor" />
              </svg>
            </button>
          )}
          {embed && playing && (
            <iframe className="embed nodrag nopan" src={embed} title={node.title || 'video'}
              onDoubleClick={(e) => e.stopPropagation()}
              allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
              allowFullScreen />
          )}
        </div>
      )}
      <div className="link-head">
        <span className="fav" style={{ background: accentOf(node) }} />
        <span className="host">{node.content.host}</span>
        <a className="icon-btn nodrag" href={node.content.url} target="_blank" rel="noreferrer"
          title="Open in browser" onClick={(e) => e.stopPropagation()}>
          <Icon name="external" size={12} />
        </a>
      </div>
      <NodeText node={node} placeholder="Untitled link"
        bodyPlaceholder="Why does this matter?" />
      {node.content.excerpt
        ? <div className="excerpt">{node.content.excerpt}</div>
        : null}
    </Shell>
  )
}

export function FileNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  return (
    <Shell node={node} selected={selected} resizable>
      <div className="link-head">
        <span className="fav" style={{ background: accentOf(node) }} />
        <span className="host">{node.content.filename}</span>
        {boardId && node.content.sha256 ? (
          <a className="icon-btn nodrag" href={api.assetUrl(boardId, node.content.sha256)}
            download={node.content.filename} onClick={(e) => e.stopPropagation()}>
            <Icon name="download" size={12} />
          </a>
        ) : null}
      </div>
      <NodeText node={node} placeholder="Untitled file" bodyPlaceholder="What is in it?" />
    </Shell>
  )
}

export function MetaNode({ id, data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const allNodes = useStore((s) => s.nodes)
  const allEdges = useStore((s) => s.edges)
  const kids = useMemo(
    () => Object.values(allNodes).filter((n) => n.parent === id), [allNodes, id])
  const miniNodes = useMemo(() => toMini(kids), [kids])
  const miniEdges = useMemo<MiniEdge[]>(() => {
    const index = new Map(kids.map((k, i) => [k.id, i]))
    const out: MiniEdge[] = []
    for (const e of Object.values(allEdges)) {
      const a = index.get(e.source)
      const b = index.get(e.target)
      if (a !== undefined && b !== undefined && a !== b) out.push([a, b])
    }
    return out
  }, [allEdges, kids])
  const count = kids.length

  return (
    <Shell node={node} selected={selected}>
      <NodeText node={node} placeholder="Group" bodyPlaceholder="What is this group for?" />
      <div className="badge-row">
        <span className="badge">
          <Icon name="layers" size={11} />
          {count === 0 ? 'empty' : `${count} ${count === 1 ? 'node' : 'nodes'}`}
        </span>
      </div>
      {miniNodes.length > 1 && (
        <div className="meta-mini">
          <GraphMini nodes={miniNodes} edges={miniEdges} w={150} h={54} pad={11} dense />
        </div>
      )}
    </Shell>
  )
}

export function GhostNode({ data }: NodeProps) {
  const d = data as { title: string; targetId: string; onJump: (id: string) => void }
  return (
    <div className="mb-node ghost" onClick={() => d.onJump(d.targetId)} title="Jump to this node">
      <div className="body">
        <div className="title" style={{ color: 'var(--mb-text-dim)', fontWeight: 500 }}>
          {d.title || 'elsewhere'} <Icon name="external" size={11} />
        </div>
      </div>
      <Handle id="l" type="source" position={Position.Left} />
      <Handle id="r" type="source" position={Position.Right} />
    </div>
  )
}

export const nodeTypes = {
  text: TextNode, image: ImageNode, video: VideoNode,
  link: LinkNode, file: FileNode, meta: MetaNode, ghost: GhostNode,
}
