import { Handle, NodeResizer, Position, type NodeProps } from '@xyflow/react'
import { useEffect, useRef, useState } from 'react'

import { addConnected, deleteNodes, setStyle, unwrapMeta, updateNode } from './actions'
import { api } from './api'
import { Icon } from './icons'
import { childCount, useStore } from './store'
import { NODE_COLOURS } from './theme'
import type { MNode } from './types'
import { useUI } from './ui'

const PORTS: [string, Position][] = [
  ['t', Position.Top], ['r', Position.Right], ['b', Position.Bottom], ['l', Position.Left],
]

const TYPE_VAR: Record<string, string> = {
  text: 'var(--mb-type-text)', image: 'var(--mb-type-image)', video: 'var(--mb-type-video)',
  link: 'var(--mb-type-link)', file: 'var(--mb-type-file)', meta: 'var(--mb-type-meta)',
}

function accentOf(node: MNode) { return node.style.color || TYPE_VAR[node.type] }

function Ports() {
  return (
    <>
      {PORTS.map(([id, position]) => (
        <Handle key={id} id={id} type="source" position={position} />
      ))}
    </>
  )
}

function Tools({ node }: { node: MNode }) {
  const enter = useStore((s) => s.enter)
  const size = node.style.fontSize ?? 13
  return (
    <div className="node-tools nodrag nopan" onDoubleClick={(e) => e.stopPropagation()}>
      {NODE_COLOURS.slice(0, 6).map((c) => (
        <button key={c} className="icon-btn" title="Colour" onClick={() => setStyle(node, { color: c })}>
          <span className="swatch" style={{ background: c }} />
        </button>
      ))}
      <button className="icon-btn" title="Default colour" onClick={() => setStyle(node, { color: undefined })}>
        <Icon name="x" size={12} />
      </button>
      <span className="sep" />
      <button className="icon-btn" title="Smaller"
        onClick={() => setStyle(node, { fontSize: Math.max(11, size - 1) })}>
        <Icon name="minus" size={13} />
      </button>
      <button className="icon-btn" title="Larger"
        onClick={() => setStyle(node, { fontSize: Math.min(30, size + 1) })}>
        <Icon name="type" size={13} />
      </button>
      <span className="sep" />
      {node.type === 'meta' && (
        <>
          <button className="icon-btn" title="Open" onClick={() => enter(node.id)}>
            <Icon name="enter" size={13} />
          </button>
          <button className="icon-btn" title="Ungroup" onClick={() => unwrapMeta(node.id)}>
            <Icon name="layers" size={13} />
          </button>
        </>
      )}
      <button className="icon-btn" title="Add connected node" onClick={() => addConnected(node)}>
        <Icon name="branch" size={13} />
      </button>
      <button className="icon-btn danger" title="Delete" onClick={() => deleteNodes([node.id])}>
        <Icon name="trash" size={13} />
      </button>
    </div>
  )
}

function Shell({ node, selected, resizable, children }: {
  node: MNode; selected?: boolean; resizable?: boolean; children: React.ReactNode
}) {
  return (
    <>
      {resizable && (
        <NodeResizer isVisible={!!selected} minWidth={150} minHeight={60}
          lineStyle={{ borderColor: 'var(--mb-accent)' }}
          handleStyle={{ width: 7, height: 7, borderRadius: 2, background: 'var(--mb-accent)' }}
          onResizeEnd={(_, p) => updateNode(node.id, { size: [p.width, p.height] })} />
      )}
      <Tools node={node} />
      <div className={`mb-node ${node.type} ${node.provenance === 'ai' ? 'ai' : ''}`}
        style={{ width: node.size[0] ? '100%' : undefined, height: node.size[1] ? '100%' : undefined }}>
        <div className="accent" style={{ background: accentOf(node) }} />
        {children}
      </div>
      <Ports />
    </>
  )
}

// ------------------------------------------------------------------ text -- //

function useAutoHeight(value: string) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = '0px'
    el.style.height = `${el.scrollHeight}px`
  }, [value])
  return ref
}

export function TextNode({ id, data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const editing = useUI((s) => s.editingId === id)
  const setUI = useUI((s) => s.set)
  const [title, setTitle] = useState(node.title)
  const [text, setText] = useState<string>(node.content.text ?? '')
  const area = useAutoHeight(text)
  const titleRef = useRef<HTMLInputElement>(null)
  const idle = useRef<ReturnType<typeof setTimeout>>()

  useEffect(() => {
    if (!editing) { setTitle(node.title); setText(node.content.text ?? '') }
  }, [node.title, node.content.text, editing])

  useEffect(() => { if (editing) titleRef.current?.focus() }, [editing])

  const commit = () => {
    clearTimeout(idle.current)
    if (title !== node.title || text !== (node.content.text ?? '')) {
      updateNode(id, { title, content: { ...node.content, text } })
    }
  }
  const touch = () => { clearTimeout(idle.current); idle.current = setTimeout(commit, 1500) }

  const style = {
    fontSize: node.style.fontSize ?? 13,
    fontWeight: node.style.weight,
    textAlign: node.style.align,
  } as React.CSSProperties

  return (
    <Shell node={node} selected={selected} resizable>
      <div className="body" style={style} onDoubleClick={() => setUI('editingId', id)}>
        {editing ? (
          <>
            <input ref={titleRef} className="title-input nodrag" value={title} placeholder="Title"
              onChange={(e) => { setTitle(e.target.value); touch() }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); area.current?.focus() }
                if (e.key === 'Escape') { commit(); setUI('editingId', null) }
              }} />
            <textarea ref={area} className="nodrag" value={text} rows={1} placeholder="..."
              style={{ fontSize: style.fontSize }}
              onChange={(e) => { setText(e.target.value); touch() }}
              onKeyDown={(e) => {
                if (e.key === 'Escape' || (e.key === 'Enter' && (e.metaKey || e.ctrlKey))) {
                  e.preventDefault(); commit(); setUI('editingId', null)
                }
              }}
              onBlur={() => { commit(); setUI('editingId', null) }} />
          </>
        ) : (
          <>
            <div className="title" style={{ color: node.style.color }}>
              {node.title || <span style={{ color: 'var(--mb-text-faint)' }}>Untitled</span>}
            </div>
            {node.content.text ? <div className="text">{node.content.text}</div> : null}
          </>
        )}
      </div>
    </Shell>
  )
}

// ----------------------------------------------------------------- media -- //

export function ImageNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  const sha = node.content.sha256
  return (
    <Shell node={node} selected={selected} resizable>
      {sha && boardId
        ? <img className="media" src={api.assetUrl(boardId, sha)} alt={node.content.alt ?? ''} draggable={false} />
        : null}
      <div className="caption">
        <div className="title">{node.title}</div>
      </div>
    </Shell>
  )
}

export function VideoNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  const sha = node.content.sha256
  const poster = node.content.poster
  return (
    <Shell node={node} selected={selected} resizable>
      {sha && boardId ? (
        <video className="media nodrag nopan" controls preload="metadata"
          poster={poster ? api.assetUrl(boardId, poster) : undefined}
          src={api.assetUrl(boardId, sha)} />
      ) : null}
      <div className="caption">
        <div className="title">{node.title}</div>
        {node.content.duration_ms ? (
          <div className="meta-line">
            {Math.floor(node.content.duration_ms / 60000)}:
            {String(Math.floor((node.content.duration_ms % 60000) / 1000)).padStart(2, '0')}
          </div>
        ) : null}
      </div>
    </Shell>
  )
}

export function LinkNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  return (
    <Shell node={node} selected={selected} resizable>
      <div className="link-row">
        <div className="fav" style={{ background: accentOf(node), opacity: 0.75 }} />
        <div style={{ minWidth: 0 }}>
          <div className="title">{node.title}</div>
          {node.content.excerpt ? <div className="text">{node.content.excerpt}</div> : null}
          <div className="meta-line">{node.content.host}</div>
        </div>
        <a className="icon-btn nodrag" href={node.content.url} target="_blank" rel="noreferrer"
          title="Open in browser" onClick={(e) => e.stopPropagation()}>
          <Icon name="external" size={13} />
        </a>
      </div>
    </Shell>
  )
}

export function FileNode({ data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const boardId = useStore((s) => s.boardId)
  return (
    <Shell node={node} selected={selected}>
      <div className="link-row">
        <div className="fav" style={{ background: accentOf(node), opacity: 0.75 }} />
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="title">{node.title}</div>
          <div className="meta-line">{node.content.filename}</div>
        </div>
        {boardId && node.content.sha256 ? (
          <a className="icon-btn nodrag" href={api.assetUrl(boardId, node.content.sha256)}
            download={node.content.filename} onClick={(e) => e.stopPropagation()}>
            <Icon name="download" size={13} />
          </a>
        ) : null}
      </div>
    </Shell>
  )
}

// ------------------------------------------------------------- meta/ghost -- //

export function MetaNode({ id, data, selected }: NodeProps) {
  const node = (data as { node: MNode }).node
  const count = useStore((s) => childCount(s.nodes, id))
  const editing = useUI((s) => s.editingId === id)
  const setUI = useUI((s) => s.set)
  const [title, setTitle] = useState(node.title)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => { if (!editing) setTitle(node.title) }, [node.title, editing])
  useEffect(() => { if (editing) { ref.current?.focus(); ref.current?.select() } }, [editing])

  return (
    <Shell node={node} selected={selected}>
      <div className="body">
        {editing ? (
          <input ref={ref} className="title-input nodrag" value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => { updateNode(id, { title }); setUI('editingId', null) }}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === 'Escape') e.currentTarget.blur() }} />
        ) : (
          <div className="title" style={{ color: node.style.color }}>{node.title || 'Group'}</div>
        )}
        {node.content.summary ? <div className="text">{node.content.summary}</div> : null}
        <span className="badge">
          <Icon name="layers" size={11} />
          {count} {count === 1 ? 'node' : 'nodes'}
        </span>
      </div>
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
