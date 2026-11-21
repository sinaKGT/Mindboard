/** The request understood, and its steps as they run (DESIGN.md 66).
 *
 *  Above an answer: how the request was read - "You want the slide summed
 *  up" - with its task and the shape asked for, then each step the model
 *  took to get there (read the picture, search the board, work something
 *  out), live while they run and folded away once the answer is written.
 */
import { useState } from 'react'

import type { TaskPlan, TaskStep, Turn } from './askStore'

const TASK_WORDS: Record<string, string> = {
  answer: 'Answer', summarize: 'Summary', explain: 'Explanation', compare: 'Comparison',
  extract: 'List', analyze: 'Analysis', rewrite: 'Rewrite', translate: 'Translation', plan: 'Plan', quiz: 'Quiz',
}
const DO_WORDS: Record<string, string> = { read: 'Read', search: 'Search', work_out: 'Work out' }
const MARK: Record<TaskStep['status'], string> = { waiting: '○', running: '◌', done: '✓', failed: '!', stopped: '■' }

/** "Step 2 of 3: reading the picture…" - for the status line while steps run */
export function stepLine(t: Turn): string | null {
  const steps = t.task?.steps ?? []
  const at = steps.findIndex((s) => s.status === 'running')
  if (at < 0) return null
  const s = steps[at]
  return `Step ${at + 1} of ${steps.length}: ${(DO_WORDS[s.do] ?? s.do).toLowerCase()} ${s.what}…`
}

export function TaskSteps({ turn }: { turn: Turn }) {
  const t: TaskPlan | undefined = turn.task
  const [open, setOpen] = useState(false)
  if (!t || (!t.steps.length && t.task === 'answer')) return null
  const working = turn.status !== 'done' && turn.status !== 'stopped'
  const shown = working || open
  return (
    <div className="task-steps">
      <div className="task-head">
        <span className="task-kind">{TASK_WORDS[t.task] ?? t.task}</span>
        {t.output ? <span className="task-output">{t.output}</span> : null}
        {t.reading ? <span className="task-reading">{t.reading}</span> : null}
        {t.steps.length && !working ? (
          <button className="task-toggle" onClick={() => setOpen((v) => !v)}>
            {open ? 'Hide the steps' : `${t.steps.length} step${t.steps.length === 1 ? '' : 's'}`}
          </button>
        ) : null}
      </div>
      {shown && t.steps.length ? (
        <ol className="task-list">
          {t.steps.map((s, i) => (
            <li key={i} className={`task-step ${s.status}`}>
              <span className="task-mark" aria-hidden>{MARK[s.status]}</span>
              <span className="task-what"><b>{DO_WORDS[s.do] ?? s.do}</b> {s.what}
                {s.note ? <span className="task-note"> — {s.note}</span> : null}</span>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  )
}
