import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import './module-deliverable-planning.css'

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

type DeliveryModule = ProductModule & {
  derived_status: ModuleStatus
  deliverable_total: number
  deliverable_complete: number
  deliverable_blocked: number
  deliverable_in_progress: number
  progress_percent: number
}

type DeliveryDeliverable = {
  deliverable_id: string
  sequence: number
  title: string
  description: string
  status: ModuleStatus
  module_id?: string | null
  module_name?: string | null
}

type ModuleProposal = {
  name: string
  description: string
  priority: ModulePriority
  proposal_state: 'review_only'
}

type DeliverySummary = {
  module_delivery: DeliveryModule[]
  delivery_deliverables: DeliveryDeliverable[]
  module_delivery_summary: {
    modules_total: number
    modules_complete: number
    linked_deliverables_total: number
    linked_deliverables_complete: number
    linked_deliverables_blocked: number
    progress_percent: number
  }
  module_proposals: ModuleProposal[]
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
  { value: 'should_have', label: 'Should have', hint: 'Important after the core release.' },
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

async function request<T>(url: string, init: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...init.headers },
  })
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = await response.json() as ApiError
      if (body.detail) detail = body.detail
    } catch {
      // Keep the HTTP fallback.
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

function statusFor(module: ProductModule, delivery: DeliverySummary | null) {
  return delivery?.module_delivery.find((item) => item.module_id === module.module_id)?.derived_status ?? module.status
}

function summarize(modules: ProductModule[], delivery: DeliverySummary | null, fallback?: ModuleProgress): ModuleProgress {
  if (!delivery?.module_delivery.length) {
    if (fallback) return fallback
    const priorities: ModuleProgress['priorities'] = {
      must_have: { total: 0, complete: 0 },
      should_have: { total: 0, complete: 0 },
      good_to_have: { total: 0, complete: 0 },
    }
    let complete = 0
    let blocked = 0
    for (const module of modules) {
      priorities[module.priority].total += 1
      if (module.status === 'complete') {
        complete += 1
        priorities[module.priority].complete += 1
      }
      if (module.status === 'blocked') blocked += 1
    }
    return {
      total: modules.length,
      complete,
      pending: modules.length - complete,
      blocked,
      progress_percent: modules.length ? Math.round((complete / modules.length) * 100) : 0,
      priorities,
    }
  }

  const priorities: ModuleProgress['priorities'] = {
    must_have: { total: 0, complete: 0 },
    should_have: { total: 0, complete: 0 },
    good_to_have: { total: 0, complete: 0 },
  }
  let complete = 0
  let blocked = 0
  for (const item of delivery.module_delivery) {
    priorities[item.priority].total += 1
    if (item.derived_status === 'complete') {
      complete += 1
      priorities[item.priority].complete += 1
    }
    if (item.derived_status === 'blocked') blocked += 1
  }
  return {
    total: delivery.module_delivery.length,
    complete,
    pending: delivery.module_delivery.length - complete,
    blocked,
    progress_percent: delivery.module_delivery_summary.linked_deliverables_total
      ? delivery.module_delivery_summary.progress_percent
      : (delivery.module_delivery.length ? Math.round((complete / delivery.module_delivery.length) * 100) : 0),
    priorities,
  }
}

export default function SolutionModules({ productId, modules, progress, canEdit, onChanged }: Props) {
  const [delivery, setDelivery] = useState<DeliverySummary | null>(null)
  const [adding, setAdding] = useState(false)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [priority, setPriority] = useState<ModulePriority>('must_have')
  const [planningModuleId, setPlanningModuleId] = useState<string | null>(null)
  const [deliverableTitle, setDeliverableTitle] = useState('')
  const [deliverableDescription, setDeliverableDescription] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const [message, setMessage] = useState('')

  const loadDelivery = useCallback(async () => {
    try {
      const response = await fetch(`/api/v1/products/${productId}/engineering`, { credentials: 'same-origin' })
      if (!response.ok) throw new Error(`Module delivery unavailable (${response.status})`)
      setDelivery(await response.json() as DeliverySummary)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to load module delivery context.')
    }
  }, [productId])

  useEffect(() => { void loadDelivery() }, [loadDelivery, modules.length])

  const summary = useMemo(() => summarize(modules, delivery, progress), [modules, delivery, progress])
  const grouped = useMemo(() => PRIORITIES.map((group) => ({
    ...group,
    modules: modules.filter((module) => module.priority === group.value),
  })), [modules])
  const unassigned = delivery?.delivery_deliverables.filter((item) => !item.module_id) ?? []

  async function refreshAll() {
    await onChanged()
    await loadDelivery()
  }

  async function addModule(event: FormEvent) {
    event.preventDefault()
    if (!name.trim() || busyId) return
    setBusyId('create')
    setMessage('')
    try {
      await request(`/api/v1/products/${productId}/modules`, {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), description: description.trim(), priority }),
      })
      setName('')
      setDescription('')
      setPriority('must_have')
      setAdding(false)
      await refreshAll()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to add module.')
    } finally {
      setBusyId(null)
    }
  }

  async function addProposal(proposal: ModuleProposal) {
    if (busyId) return
    setBusyId(`proposal:${proposal.name}`)
    setMessage('')
    try {
      await request(`/api/v1/products/${productId}/modules`, {
        method: 'POST',
        body: JSON.stringify(proposal),
      })
      await refreshAll()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to add suggested module.')
    } finally {
      setBusyId(null)
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
      await refreshAll()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to update module.')
    } finally {
      setBusyId(null)
    }
  }

  async function removeModule(module: ProductModule) {
    if (busyId || !window.confirm(`Remove “${module.name}” from the intended module roadmap?`)) return
    setBusyId(module.module_id)
    try {
      await request(`/api/v1/products/${productId}/modules/${module.module_id}`, { method: 'DELETE' })
      await refreshAll()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to remove module.')
    } finally {
      setBusyId(null)
    }
  }

  function beginDeliverable(module: ProductModule) {
    setPlanningModuleId(module.module_id)
    setDeliverableTitle('')
    setDeliverableDescription('')
    setMessage('')
  }

  function cancelDeliverable() {
    setPlanningModuleId(null)
    setDeliverableTitle('')
    setDeliverableDescription('')
  }

  async function createDeliverable(event: FormEvent, module: ProductModule) {
    event.preventDefault()
    if (!deliverableTitle.trim() || busyId) return
    const busyKey = `deliverable:${module.module_id}`
    setBusyId(busyKey)
    setMessage('')
    try {
      setDelivery(await request<DeliverySummary>(
        `/api/v1/products/${productId}/engineering/modules/${module.module_id}/deliverables`,
        {
          method: 'POST',
          body: JSON.stringify({
            title: deliverableTitle.trim(),
            description: deliverableDescription.trim(),
          }),
        },
      ))
      cancelDeliverable()
      await onChanged()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to create module deliverable.')
    } finally {
      setBusyId(null)
    }
  }

  async function assignDeliverable(module: ProductModule, deliverableId: string) {
    if (!deliverableId || busyId) return
    setBusyId(module.module_id)
    try {
      setDelivery(await request<DeliverySummary>(
        `/api/v1/products/${productId}/engineering/deliverables/${deliverableId}/module`,
        { method: 'PUT', body: JSON.stringify({ module_id: module.module_id }) },
      ))
      await onChanged()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to attach deliverable.')
    } finally {
      setBusyId(null)
    }
  }

  async function detachDeliverable(module: ProductModule, deliverableId: string) {
    if (busyId) return
    setBusyId(module.module_id)
    try {
      setDelivery(await request<DeliverySummary>(
        `/api/v1/products/${productId}/engineering/deliverables/${deliverableId}/module`,
        { method: 'PUT', body: JSON.stringify({ module_id: null }) },
      ))
      await onChanged()
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to detach deliverable.')
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
          <p>Approve the functional scope, plan module deliverables, and track what is closed or pending.</p>
        </div>
        {canEdit && <button className="studio-secondary" type="button" onClick={() => setAdding((value) => !value)}>{adding ? 'Close' : '+ Add module'}</button>}
      </div>

      <div className="studio-module-summary" aria-label="Module progress summary">
        <article><span>Closed</span><strong>{summary.complete}</strong><small>of {summary.total} modules</small></article>
        <article><span>Pending</span><strong>{summary.pending}</strong><small>not yet complete</small></article>
        <article><span>Blocked</span><strong>{summary.blocked}</strong><small>requires attention</small></article>
        <article><span>Module progress</span><strong>{summary.progress_percent}%</strong><small>accepted deliverables</small></article>
      </div>

      <div className="studio-module-priority-summary">
        {PRIORITIES.map((item) => <div className={`priority-${item.value}`} key={item.value}><span>{item.label}</span><strong>{summary.priorities[item.value].complete}/{summary.priorities[item.value].total} closed</strong></div>)}
      </div>

      {adding && (
        <form className="studio-module-form" onSubmit={addModule}>
          <div className="studio-module-form-copy"><strong>Add an intended module</strong><span>Capture a business or functional area users expect.</span></div>
          <label>Module name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={160} required /></label>
          <label>Priority<select value={priority} onChange={(event) => setPriority(event.target.value as ModulePriority)}>{PRIORITIES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
          <label className="studio-module-description-field">Description<textarea value={description} onChange={(event) => setDescription(event.target.value)} rows={2} maxLength={2000} /></label>
          <button className="studio-primary" disabled={Boolean(busyId) || !name.trim()}>{busyId === 'create' ? 'Adding module…' : 'Add to roadmap'}</button>
        </form>
      )}

      {message && <div className="studio-error" role="alert">{message}</div>}

      {canEdit && Boolean(delivery?.module_proposals.length) && (
        <section className="studio-module-proposals">
          <div><span className="studio-eyebrow">REVIEW-ONLY SUGGESTIONS</span><strong>Suggested starting modules</strong><p>Nothing becomes approved scope until you add it.</p></div>
          <div className="studio-module-proposal-grid">
            {delivery?.module_proposals.map((proposal) => (
              <article key={proposal.name}>
                <span className={`studio-priority-badge priority-${proposal.priority}`}>{priorityLabel(proposal.priority)}</span>
                <strong>{proposal.name}</strong><p>{proposal.description}</p>
                <button className="studio-secondary" type="button" disabled={Boolean(busyId)} onClick={() => void addProposal(proposal)}>Add to roadmap</button>
              </article>
            ))}
          </div>
        </section>
      )}

      {modules.length === 0 ? (
        <div className="studio-modules-empty"><strong>No intended modules approved yet.</strong><p>Review the suggestions above or add the modules expected in this solution.</p></div>
      ) : (
        <div className="studio-module-groups">
          {grouped.map((group) => (
            <section className={`studio-module-group priority-${group.value}`} key={group.value}>
              <header><div><strong>{group.label}</strong><span>{group.hint}</span></div><small>{group.modules.filter((module) => statusFor(module, delivery) === 'complete').length}/{group.modules.length} closed</small></header>
              {group.modules.length ? <div className="studio-module-list">
                {group.modules.map((module) => {
                  const deliveryModule = delivery?.module_delivery.find((item) => item.module_id === module.module_id)
                  const linked = delivery?.delivery_deliverables.filter((item) => item.module_id === module.module_id) ?? []
                  const derivedStatus = deliveryModule?.derived_status ?? module.status
                  const percent = deliveryModule?.progress_percent ?? (module.status === 'complete' ? 100 : 0)
                  const creatingDeliverable = planningModuleId === module.module_id
                  const busyCreating = busyId === `deliverable:${module.module_id}`
                  return (
                    <article className={`studio-module-row status-${derivedStatus}`} key={module.module_id}>
                      <div className="studio-module-sequence">{String(module.sequence).padStart(2, '0')}</div>
                      <div className="studio-module-copy">
                        <strong>{module.name}</strong><p>{module.description || 'Description to be refined during solution planning.'}</p>
                        <span className={`studio-priority-badge priority-${module.priority}`}>{priorityLabel(module.priority)}</span>
                        <div className="studio-module-delivery">
                          <div className="studio-module-delivery-heading"><strong>Delivery</strong><span>{deliveryModule?.deliverable_complete ?? 0}/{deliveryModule?.deliverable_total ?? 0} complete · {percent}%</span></div>
                          <div className="studio-module-delivery-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{ width: `${percent}%` }} /></div>
                          {linked.length ? <div className="studio-module-deliverables">{linked.map((item) => <div key={item.deliverable_id}><span className={`studio-status-dot status-${item.status}`} /><strong>{item.title}</strong><small>{STATUS_LABELS[item.status]}</small>{canEdit && <button type="button" disabled={Boolean(busyId)} onClick={() => void detachDeliverable(module, item.deliverable_id)}>Detach</button>}</div>)}</div> : <small>No deliverables planned for this module yet.</small>}

                          {canEdit && (
                            <div className="studio-module-work-planning">
                              {creatingDeliverable ? (
                                <form className="studio-module-deliverable-form" onSubmit={(event) => void createDeliverable(event, module)}>
                                  <div className="studio-module-deliverable-form-heading">
                                    <strong>New deliverable</strong>
                                    <span>Create planned work directly under {module.name}.</span>
                                  </div>
                                  <label>
                                    Deliverable title
                                    <input
                                      autoFocus
                                      value={deliverableTitle}
                                      onChange={(event) => setDeliverableTitle(event.target.value)}
                                      maxLength={200}
                                      placeholder="e.g. Assessment and grading workflow"
                                      required
                                    />
                                  </label>
                                  <label>
                                    Description <small>optional</small>
                                    <textarea
                                      value={deliverableDescription}
                                      onChange={(event) => setDeliverableDescription(event.target.value)}
                                      maxLength={4000}
                                      rows={2}
                                      placeholder="Define the result Engineering Space should deliver."
                                    />
                                  </label>
                                  <div className="studio-module-deliverable-form-actions">
                                    <button className="studio-primary" type="submit" disabled={Boolean(busyId) || !deliverableTitle.trim()}>{busyCreating ? 'Creating…' : 'Create deliverable'}</button>
                                    <button className="studio-secondary" type="button" disabled={Boolean(busyId)} onClick={cancelDeliverable}>Cancel</button>
                                  </div>
                                </form>
                              ) : (
                                <button className="studio-module-new-deliverable" type="button" disabled={Boolean(busyId)} onClick={() => beginDeliverable(module)}>+ New deliverable</button>
                              )}

                              <label className="studio-module-attach-deliverable">
                                Attach existing deliverable
                                <select defaultValue="" disabled={Boolean(busyId) || !unassigned.length} onChange={(event) => { const value = event.target.value; event.target.value = ''; void assignDeliverable(module, value) }}>
                                  <option value="">{unassigned.length ? 'Select unassigned deliverable…' : 'All existing deliverables assigned'}</option>
                                  {unassigned.map((item) => <option value={item.deliverable_id} key={item.deliverable_id}>{item.sequence}. {item.title}</option>)}
                                </select>
                              </label>
                            </div>
                          )}
                        </div>
                      </div>
                      {canEdit ? <div className="studio-module-controls">
                        <label>Priority<select value={module.priority} disabled={Boolean(busyId)} onChange={(event) => void updateModule(module, { priority: event.target.value as ModulePriority })}>{PRIORITIES.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
                        {deliveryModule?.deliverable_total ? <div className="studio-derived-progress"><span>Progress</span><strong>{STATUS_LABELS[derivedStatus]}</strong><small>Derived from accepted deliverables</small></div> : <label>Progress<select value={module.status} disabled={Boolean(busyId)} onChange={(event) => void updateModule(module, { status: event.target.value as ModuleStatus })}>{(Object.keys(STATUS_LABELS) as ModuleStatus[]).map((status) => <option value={status} key={status}>{STATUS_LABELS[status]}</option>)}</select></label>}
                        <button className="studio-module-remove" type="button" disabled={Boolean(busyId)} onClick={() => void removeModule(module)}>Remove</button>
                      </div> : <span className={`studio-status-label status-${derivedStatus}`}>{STATUS_LABELS[derivedStatus]}</span>}
                    </article>
                  )
                })}
              </div> : <p className="studio-module-group-empty">No modules in this priority yet.</p>}
            </section>
          ))}
        </div>
      )}
    </section>
  )
}
