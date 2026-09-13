import { FormEvent, useMemo, useState } from 'react'

export type ModulePriority = 'must_have' | 'should_have' | 'good_to_have'
export type ModuleStatus = 'planned' | 'in_progress' | 'blocked' | 'complete'

export type ProductModule = {
  module_id: string
  sequence: number
  name: string
  description: string
  priority: ModulePriority
  status: ModuleStatus
  created_at: string
  updated_at: string
}

export type ModuleProgress = {
  total: number
  complete: number
  pending: number
  blocked: number
  progress_percent: number
  priorities: Record<ModulePriority, { total: number; complete: number }>
}

type Props = {
  productId: string
  modules: ProductModule[]
  progress?: ModuleProgress
  canEdit: boolean
  onChanged: () => Promise<void>
}

type ApiError = { detail?: string }

const PRIORITIES: Array<{ value: ModulePriority; label: string; hint: string }> = [
  { value: 'must_have', label: 'Must have', hint: 'Essential for the solution to be viable.' },
  { value: 'should_have', label: 'Should have', hint: 'Important, but can follow the core release.' },
  { value: 'good_to_have', label: 'Good to have', hint: 'Adds value after higher priorities are secure.' },
]

const STATUS_LABELS: Record<ModuleStatus, string> = {
  planned: 'Planned',
  in_progress: 'In progress',
  blocked: 'Blocked',
  complete: 'Complete',
}

function priorityLabel(value: ModulePriority) {
  return PRIORITIES.find((item) => item.value === value)?.label ?? value
}

async function request(url: string, init: RequestInit) {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const response = await fetch(url, {
    ...init,
    credentials: 'same-origin',
    headers,
  })
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as ApiError
      if (body.detail) detail = body.detail
    } catch {
      // Keep the HTTP fallback.
    }
    throw new Error(detail)
  }
  return response.json()
}

function fallbackProgress(modules: ProductModule[]): ModuleProgress {
  const result: ModuleProgress = {
    total: modules.length,
    complete: 0,
    pending: 0,
    blocked: 0,
    progress_percent: 0,
    priorities: {
      must_have: { total: 0, complete: 0 },
      should_have: { total: 0, complete: 0 },
      good_to_have: { total: 0, complete: 0 },
    },
  }
  for (const module of modules) {
    result.priorities[module.priority].total += 1
    if (module.status === 'complete') {
      result.complete += 1
      result.priorities[module.priority].complete += 1
    } else {
      result.pending += 1
    }
    if (module.status === 'blocked') result.blocked += 1
  }
  if (result.total) result.progress_percent = Math.round((result.complete / result.total) * 100)
  return result
}

export default function SolutionModules({ productId, modules, progress, canEdit, onChanged }: Props) {
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<ModulePriority>('must_have')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [message, setMessage] = useState('')

  const summary = progress ?? fallbackProgress(modules)
  const grouped = useMemo(() => PRIORITIES.map((priorityOption) => ({
    ...priorityOption,
    modules: modules.filter((module) => module.priority === priorityOption.value),
  })), [modules])

  async function createModule(event: FormEvent) {
    event.preventDefault()
    if (!name.trim() || creating) return
    setCreating(true)
    setMessage('')
    try {
      await request(`/api/v1/products/${productId}/modules`, {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          description: description.trim(),
          priority,
        }),
      })
      setName('')
      setDescription('')
      setPriority('must_have')
      setAdding(false)
      await onChanged()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to add module.')
    } finally {
      setCreating(false)
    }
  }

  async function updateModule(module: ProductModule, changes: Partial<Pick<ProductModule, 'priority' | 'status'>>) {
    if (busyId) return
    setBusyId(module.module_id)
    setMessage('')
    try {
      await request(`/api/v1/products/${productId}/modules/${module.module_id}`, {
        method: 'PATCH',
        body: JSON.stringify(changes),
      })
      await onChanged()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to update module.')
    } finally {
      setBusyId(null)
    }
  }

  async function removeModule(module: ProductModule) {
    if (busyId || !window.confirm(`Remove “${module.name}” from the intended module roadmap?`)) return
    setBusyId(module.module_id)
    setMessage('')
    try {
      await request(`/api/v1/products/${productId}/modules/${module.module_id}`, { method: 'DELETE' })
      await onChanged()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to remove module.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="studio-card studio-modules" aria-labelledby="solution-modules-heading">
      <div className="studio-card-heading studio-modules-heading">
        <div>
          <span className="studio-eyebrow">SOLUTION DEVELOPMENT</span>
          <h2 id="solution-modules-heading">Intended modules</h2>
          <p>Review what the solution is expected to contain, what is closed, and what remains pending.</p>
        </div>
        {canEdit && (
          <button className="studio-secondary studio-module-add-button" type="button" onClick={() => setAdding((value) => !value)}>
            {adding ? 'Close' : '+ Add module'}
          </button>
        )}
      </div>

      <div className="studio-module-summary" aria-label="Module progress summary">
        <article><span>Closed</span><strong>{summary.complete}</strong><small>of {summary.total} modules</small></article>
        <article><span>Pending</span><strong>{summary.pending}</strong><small>not yet complete</small></article>
        <article><span>Blocked</span><strong>{summary.blocked}</strong><small>requires attention</small></article>
        <article><span>Module progress</span><strong>{summary.progress_percent}%</strong><small>closed modules</small></article>
      </div>

      <div className="studio-module-priority-summary">
        {PRIORITIES.map((item) => {
          const prioritySummary = summary.priorities[item.value]
          return (
            <div className={`priority-${item.value}`} key={item.value}>
              <span>{item.label}</span>
              <strong>{prioritySummary.complete}/{prioritySummary.total} closed</strong>
            </div>
          )
        })}
      </div>

      {adding && (
        <form className="studio-module-form" onSubmit={createModule}>
          <div className="studio-module-form-copy">
            <strong>Add an intended module</strong>
            <span>Capture the business or functional area users expect the solution to provide.</span>
          </div>
          <label>
            Module name
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Farm Registry" maxLength={160} required />
          </label>
          <label>
            Priority
            <select value={priority} onChange={(event) => setPriority(event.target.value as ModulePriority)}>
              {PRIORITIES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label className="studio-module-description-field">
            Description <span>optional</span>
            <textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What should this module enable?" rows={2} maxLength={2000} />
          </label>
          <button className="studio-primary" disabled={creating || !name.trim()}>
            {creating ? 'Adding module…' : 'Add to roadmap'}
          </button>
        </form>
      )}

      {message && <div className="studio-error" role="alert">{message}</div>}

      {modules.length === 0 ? (
        <div className="studio-modules-empty">
          <strong>No intended modules captured yet.</strong>
          <p>Add the business or functional modules expected in this solution, then classify each as Must have, Should have or Good to have.</p>
          {canEdit && !adding && <button className="studio-secondary" type="button" onClick={() => setAdding(true)}>Add the first module</button>}
        </div>
      ) : (
        <div className="studio-module-groups">
          {grouped.map((group) => (
            <section className={`studio-module-group priority-${group.value}`} key={group.value}>
              <header>
                <div><strong>{group.label}</strong><span>{group.hint}</span></div>
                <small>{group.modules.filter((module) => module.status === 'complete').length}/{group.modules.length} closed</small>
              </header>
              {group.modules.length ? (
                <div className="studio-module-list">
                  {group.modules.map((module) => (
                    <article className={`studio-module-row status-${module.status}`} key={module.module_id}>
                      <div className="studio-module-sequence">{String(module.sequence).padStart(2, '0')}</div>
                      <div className="studio-module-copy">
                        <strong>{module.name}</strong>
                        <p>{module.description || 'Description to be refined during solution planning.'}</p>
                        <span className={`studio-priority-badge priority-${module.priority}`}>{priorityLabel(module.priority)}</span>
                      </div>
                      {canEdit ? (
                        <div className="studio-module-controls">
                          <label>
                            Priority
                            <select
                              value={module.priority}
                              disabled={Boolean(busyId)}
                              onChange={(event) => void updateModule(module, { priority: event.target.value as ModulePriority })}
                              aria-label={`Priority for ${module.name}`}
                            >
                              {PRIORITIES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}
                            </select>
                          </label>
                          <label>
                            Progress
                            <select
                              value={module.status}
                              disabled={Boolean(busyId)}
                              onChange={(event) => void updateModule(module, { status: event.target.value as ModuleStatus })}
                              aria-label={`Progress for ${module.name}`}
                            >
                              {(Object.keys(STATUS_LABELS) as ModuleStatus[]).map((status) => (
                                <option value={status} key={status}>{STATUS_LABELS[status]}</option>
                              ))}
                            </select>
                          </label>
                          <button className="studio-module-remove" type="button" disabled={Boolean(busyId)} onClick={() => void removeModule(module)}>Remove</button>
                          {busyId === module.module_id && <small role="status">Saving…</small>}
                        </div>
                      ) : (
                        <span className={`studio-status-label status-${module.status}`}>{STATUS_LABELS[module.status]}</span>
                      )}
                    </article>
                  ))}
                </div>
              ) : <p className="studio-module-group-empty">No modules in this priority yet.</p>}
            </section>
          ))}
        </div>
      )}
    </section>
  )
}
