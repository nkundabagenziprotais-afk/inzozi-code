import {
  ReactNode,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'

type WorkStatus = 'planned' | 'in_progress' | 'blocked' | 'complete'

type ProductModule = {
  module_id: string
  name: string
  priority: string
  status: WorkStatus
}

type Deliverable = {
  deliverable_id: string
  title: string
  status: WorkStatus
}

type ProductSnapshot = {
  product_id: string
  name: string
  modules?: ProductModule[]
  deliverables: Deliverable[]
}

type ProductSummary = {
  product_id: string
  name: string
}

type EngineeringBinding = {
  binding_id: string
  product_id: string
  module_id?: string | null
  module_name?: string | null
  deliverable_id?: string | null
  deliverable_title?: string | null
  repository_url?: string | null
  workspace_id?: string | null
  repository_ref?: string | null
  sync_status: 'ready' | 'active' | 'attention'
  updated_at: string
}

type EngineeringEvent = {
  event_id: string
  event_type: string
  status: 'info' | 'success' | 'failure'
  summary: string
  module_name?: string | null
  deliverable_title?: string | null
  created_at: string
}

type ModuleDelivery = {
  module_id: string
  sequence: number
  name: string
  priority: string
  status: WorkStatus
  derived_status: WorkStatus
  deliverable_total: number
  deliverable_complete: number
  deliverable_blocked: number
  deliverable_in_progress: number
  progress_percent: number
}

type DeliveryDeliverable = Deliverable & {
  sequence: number
  description: string
  module_id?: string | null
  module_name?: string | null
}

type ModuleDeliverySummary = {
  modules_total: number
  modules_complete: number
  linked_deliverables_total: number
  linked_deliverables_complete: number
  linked_deliverables_blocked: number
  progress_percent: number
}

type ModuleProposal = {
  name: string
  description: string
  priority: 'must_have' | 'should_have' | 'good_to_have'
  proposal_state: 'review_only'
}

type EngineeringSummary = {
  binding: EngineeringBinding | null
  recent_events: EngineeringEvent[]
  evidence_count: number
  last_activity_at: string | null
  sync_health: 'not_linked' | 'ready' | 'active' | 'attention'
  module_delivery: ModuleDelivery[]
  delivery_deliverables: DeliveryDeliverable[]
  module_delivery_summary: ModuleDeliverySummary
  module_proposals: ModuleProposal[]
}

type Session = {
  permissions?: string[]
}

type Surface = 'control' | 'engineering'

type ApiError = { detail?: string }

type FetchLike = typeof window.fetch

const SYNC_PREFIX = '/api/v1/products/'

function requestUrl(input: RequestInfo | URL) {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return input.url
}

function methodOf(input: RequestInfo | URL, init?: RequestInit) {
  if (init?.method) return init.method.toUpperCase()
  if (typeof Request !== 'undefined' && input instanceof Request) return input.method.toUpperCase()
  return 'GET'
}

function parseJsonBody(init?: RequestInit): Record<string, unknown> {
  if (typeof init?.body !== 'string') return {}
  try {
    return JSON.parse(init.body) as Record<string, unknown>
  } catch {
    return {}
  }
}

function shortRepository(value?: string | null) {
  if (!value) return 'Repository not linked yet'
  return value.replace(/^https:\/\/github\.com\//, '').replace(/\.git$/, '')
}

function timeLabel(value?: string | null) {
  if (!value) return 'No engineering activity yet'
  try {
    return new Date(value).toLocaleString([], {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return value
  }
}

function eventLabel(value: string) {
  return value.replaceAll('_', ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

async function responseJson<T>(response: Response): Promise<T | null> {
  try {
    return await response.clone().json() as T
  } catch {
    return null
  }
}

export default function InzoziSolutionSync({ children }: { children: ReactNode }) {
  const [activeProduct, setActiveProduct] = useState<ProductSnapshot | null>(null)
  const [summary, setSummary] = useState<EngineeringSummary | null>(null)
  const [moduleId, setModuleId] = useState('')
  const [deliverableId, setDeliverableId] = useState('')
  const [surface, setSurface] = useState<Surface>('control')
  const [canEdit, setCanEdit] = useState(false)
  const [message, setMessage] = useState('')
  const [detailsOpen, setDetailsOpen] = useState(false)

  const originalFetch = useRef<FetchLike | null>(null)
  const activeProductRef = useRef<ProductSnapshot | null>(null)
  const moduleIdRef = useRef('')
  const deliverableIdRef = useRef('')
  const surfaceRef = useRef<Surface>('control')
  const contextSelectionTouchedRef = useRef(false)
  const handoffApprovedRef = useRef(false)
  const handoffInFlightRef = useRef(false)

  function syncProductState(product: ProductSnapshot) {
    activeProductRef.current = product
    setActiveProduct(product)
  }

  const rawFetch = useCallback((input: RequestInfo | URL, init?: RequestInit) => {
    const implementation = originalFetch.current ?? window.fetch.bind(window)
    return implementation(input, init)
  }, [])

  const loadEngineeringSummary = useCallback(async (productId: string) => {
    try {
      const response = await rawFetch(`/api/v1/products/${productId}/engineering`, {
        credentials: 'same-origin',
      })
      if (!response.ok) throw new Error(`Synchronization unavailable (${response.status})`)
      const payload = await response.json() as EngineeringSummary
      setSummary(payload)
      if (!contextSelectionTouchedRef.current) {
        const nextModule = payload.binding?.module_id ?? ''
        const nextDeliverable = payload.binding?.deliverable_id ?? ''
        moduleIdRef.current = nextModule
        deliverableIdRef.current = nextDeliverable
        setModuleId(nextModule)
        setDeliverableId(nextDeliverable)
      }
      setMessage('')
      return payload
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to read engineering synchronization.')
      return null
    }
  }, [rawFetch])

  const refreshProduct = useCallback(async (productId: string) => {
    try {
      const response = await rawFetch(`/api/v1/products/${productId}`, {
        credentials: 'same-origin',
      })
      if (!response.ok) return
      const product = await response.json() as ProductSnapshot
      syncProductState(product)
    } catch {
      // Product Control remains usable even if this convenience refresh fails.
    }
  }, [rawFetch])

  const updateBinding = useCallback(async (
    productId: string,
    changes: Record<string, string | null>,
  ) => {
    try {
      const response = await rawFetch(`/api/v1/products/${productId}/engineering/binding`, {
        method: 'PUT',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(changes),
      })
      if (!response.ok) {
        const body = await responseJson<ApiError>(response)
        throw new Error(body?.detail ?? `Unable to synchronize context (${response.status})`)
      }
      const payload = await response.json() as EngineeringSummary
      setSummary(payload)
      setMessage('')
      return payload
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to synchronize engineering context.')
      return null
    }
  }, [rawFetch])

  const assignDeliverable = useCallback(async (
    productId: string,
    targetDeliverableId: string,
    targetModuleId: string | null,
  ) => {
    try {
      const response = await rawFetch(
        `/api/v1/products/${productId}/engineering/deliverables/${targetDeliverableId}/module`,
        {
          method: 'PUT',
          credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ module_id: targetModuleId }),
        },
      )
      if (!response.ok) {
        const body = await responseJson<ApiError>(response)
        throw new Error(body?.detail ?? `Unable to assign deliverable (${response.status})`)
      }
      const payload = await response.json() as EngineeringSummary
      setSummary(payload)
      setMessage('')
      return payload
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to assign deliverable to module.')
      return null
    }
  }, [rawFetch])

  const recordEvent = useCallback(async (
    productId: string,
    eventType: string,
    status: 'info' | 'success' | 'failure',
    eventSummary: string,
    evidence: Record<string, unknown> = {},
  ) => {
    try {
      const response = await rawFetch(`/api/v1/products/${productId}/engineering/events`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_type: eventType,
          status,
          summary: eventSummary,
          evidence,
        }),
      })
      if (!response.ok) {
        const body = await responseJson<ApiError>(response)
        throw new Error(body?.detail ?? `Unable to record engineering evidence (${response.status})`)
      }
      const payload = await response.json() as EngineeringSummary
      setSummary(payload)
      setMessage('')
      await refreshProduct(productId)
      return payload
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Engineering work succeeded, but progress synchronization failed.')
      return null
    }
  }, [rawFetch, refreshProduct])

  const captureProductResponse = useCallback(async (response: Response) => {
    const payload = await responseJson<ProductSnapshot>(response)
    if (!payload?.product_id || !Array.isArray(payload.deliverables)) return
    const changedProduct = activeProductRef.current?.product_id !== payload.product_id
    syncProductState(payload)
    if (changedProduct) {
      contextSelectionTouchedRef.current = false
      moduleIdRef.current = ''
      deliverableIdRef.current = ''
      setModuleId('')
      setDeliverableId('')
      setSummary(null)
    }
    await loadEngineeringSummary(payload.product_id)
  }, [loadEngineeringSummary])

  const captureProductList = useCallback(async (response: Response) => {
    if (activeProductRef.current) return
    const payload = await responseJson<{ products?: ProductSummary[] }>(response)
    const preferred = payload?.products?.find((item) => item.name === 'Inzozi AI-Coding')
    if (!preferred) return
    await refreshProduct(preferred.product_id)
    await loadEngineeringSummary(preferred.product_id)
  }, [loadEngineeringSummary, refreshProduct])

  const captureEngineeringResult = useCallback(async (
    url: string,
    method: string,
    init: RequestInit | undefined,
    response: Response,
  ) => {
    const product = activeProductRef.current
    if (!product || !response.ok) return
    if (!url.includes('/api/v1/workspaces') && !url.includes('/api/v1/agent/run')) return

    const requestBody = parseJsonBody(init)
    const payload = await responseJson<Record<string, unknown>>(response)
    if (!payload) return

    if (method === 'POST' && /\/api\/v1\/workspaces(?:\?|$)/.test(url)) {
      const workspaceId = typeof payload.workspace_id === 'string' ? payload.workspace_id : null
      const repositoryUrl = typeof requestBody.repository_url === 'string' ? requestBody.repository_url : null
      const repositoryRef = typeof requestBody.ref === 'string' ? requestBody.ref : null
      await updateBinding(product.product_id, {
        module_id: moduleIdRef.current || null,
        deliverable_id: deliverableIdRef.current || null,
        repository_url: repositoryUrl,
        workspace_id: workspaceId,
        repository_ref: repositoryRef,
      })
      await recordEvent(
        product.product_id,
        'workspace_opened',
        'success',
        'Guarded Engineering Space workspace opened.',
        { workspace_id: workspaceId, repository_url: repositoryUrl, repository_ref: repositoryRef },
      )
      return
    }

    if (method === 'POST' && /\/actions(?:\?|$)/.test(url)) {
      const exitCode = typeof payload.exit_code === 'number' ? payload.exit_code : null
      const action = typeof requestBody.action === 'string' ? requestBody.action : 'workspace action'
      await recordEvent(
        product.product_id,
        'command_completed',
        exitCode === 0 ? 'success' : 'failure',
        `${action} ${exitCode === 0 ? 'completed successfully' : 'failed'}.`,
        {
          action,
          exit_code: exitCode,
          timed_out: Boolean(payload.timed_out),
        },
      )
      return
    }

    if (method === 'POST' && /\/git\/commit\/prepare(?:\?|$)/.test(url)) {
      await recordEvent(
        product.product_id,
        'git_review_prepared',
        'info',
        'Git change review prepared for human approval.',
        {
          branch: payload.branch,
          head: payload.head,
          changed_paths: payload.changed_paths,
        },
      )
      return
    }

    if (method === 'POST' && /\/git\/commit\/approve(?:\?|$)/.test(url)) {
      await recordEvent(
        product.product_id,
        'commit_created',
        'success',
        'Reviewed local commit created.',
        {
          branch: payload.branch,
          commit_sha: payload.commit_sha,
          pushed: payload.pushed,
        },
      )
      return
    }

    if (method === 'POST' && /\/git\/push\/approve(?:\?|$)/.test(url)) {
      await recordEvent(
        product.product_id,
        'push_completed',
        'success',
        'Reviewed commit pushed to the remote repository.',
        {
          branch: payload.branch,
          commit_sha: payload.commit_sha,
          repository_url: payload.repository_url,
        },
      )
      return
    }

    if (method === 'POST' && /\/git\/pull-request\/approve(?:\?|$)/.test(url)) {
      const number = typeof payload.pull_request_number === 'number' ? ` #${payload.pull_request_number}` : ''
      await recordEvent(
        product.product_id,
        'pull_request_created',
        'success',
        `Draft pull request${number} created or synchronized.`,
        {
          pull_request_number: payload.pull_request_number,
          pull_request_url: payload.pull_request_url,
          head_branch: payload.head_branch,
          base_branch: payload.base_branch,
          commit_sha: payload.commit_sha,
        },
      )
      return
    }

    if (method === 'POST' && url.includes('/api/v1/agent/run')) {
      const mode = typeof requestBody.mode === 'string' ? requestBody.mode : 'agent'
      if (mode === 'build' || mode === 'debug') {
        await recordEvent(
          product.product_id,
          'agent_run_completed',
          'success',
          `Aquila ${mode} run completed in Engineering Space.`,
          {
            mode,
            provider_alias: payload.provider_alias,
            checkpoint_id: payload.checkpoint_id,
          },
        )
      }
    }
  }, [recordEvent, updateBinding])

  useLayoutEffect(() => {
    const nativeFetch = window.fetch.bind(window)
    originalFetch.current = nativeFetch

    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input)
      const method = methodOf(input, init)
      const response = await nativeFetch(input, init)

      if (url.includes('/engineering')) return response

      if (url === '/api/v1/products' && method === 'GET' && response.ok) {
        void captureProductList(response)
      } else if (
        url.startsWith(SYNC_PREFIX)
        && !url.includes('/engineering')
        && response.ok
      ) {
        void captureProductResponse(response)
      }

      if (surfaceRef.current === 'engineering') {
        void captureEngineeringResult(url, method, init, response)
      }
      return response
    }

    return () => {
      window.fetch = nativeFetch
      originalFetch.current = null
    }
  }, [captureEngineeringResult, captureProductList, captureProductResponse])

  useEffect(() => {
    void rawFetch('/api/v1/auth/me', { credentials: 'same-origin' })
      .then(async (response) => {
        if (!response.ok) return
        const session = await response.json() as Session
        setCanEdit(Boolean(session.permissions?.includes('workspace:edit')))
      })
      .catch(() => setCanEdit(false))
  }, [rawFetch])

  useEffect(() => {
    function onClick(event: MouseEvent) {
      const target = event.target
      if (!(target instanceof Element)) return
      const button = target.closest('button')
      if (!button) return
      const text = button.textContent?.trim() ?? ''

      if (
        text.includes('Engineering Workspace')
        || text.includes('Open Engineering Workspace')
        || text.includes('Continue in Engineering Space')
      ) {
        const product = activeProductRef.current

        if (handoffApprovedRef.current) {
          handoffApprovedRef.current = false
          surfaceRef.current = 'engineering'
          setSurface('engineering')
          return
        }

        if (handoffInFlightRef.current) {
          event.preventDefault()
          event.stopImmediatePropagation()
          return
        }

        if (product && (!moduleIdRef.current || !deliverableIdRef.current)) {
          event.preventDefault()
          event.stopImmediatePropagation()
          setMessage(
            !moduleIdRef.current
              ? 'Select an intended module before continuing in Engineering Space.'
              : 'Select a deliverable for this module before continuing in Engineering Space.',
          )
          return
        }

        if (!product) return

        const selectedModuleId = moduleIdRef.current
        const selectedDeliverableId = deliverableIdRef.current
        const selectedDelivery = summary?.delivery_deliverables.find(
          (item) => item.deliverable_id === selectedDeliverableId,
        )

        if (!selectedDelivery || selectedDelivery.module_id !== selectedModuleId) {
          event.preventDefault()
          event.stopImmediatePropagation()
          setMessage(
            'The selected deliverable is not bound to the selected module. '
            + 'Product Control context was preserved; refresh and select the intended work again.',
          )
          void loadEngineeringSummary(product.product_id)
          return
        }

        event.preventDefault()
        event.stopImmediatePropagation()
        handoffInFlightRef.current = true

        void (async () => {
          const bound = await updateBinding(product.product_id, {
            module_id: selectedModuleId,
            deliverable_id: selectedDeliverableId,
          })
          handoffInFlightRef.current = false

          if (
            !bound?.binding
            || bound.binding.module_id !== selectedModuleId
            || bound.binding.deliverable_id !== selectedDeliverableId
          ) {
            setMessage(
              'Engineering handoff was stopped because the persisted module '
              + 'and deliverable context did not match the selected work.',
            )
            return
          }

          contextSelectionTouchedRef.current = true
          moduleIdRef.current = selectedModuleId
          deliverableIdRef.current = selectedDeliverableId
          setModuleId(selectedModuleId)
          setDeliverableId(selectedDeliverableId)
          setSummary(bound)
          setMessage('')

          handoffApprovedRef.current = true
          button.click()
        })()
        return
      }

      if (text.includes('Project Control Center')) {
        surfaceRef.current = 'control'
        setSurface('control')
        const product = activeProductRef.current
        if (product) {
          void refreshProduct(product.product_id)
          void loadEngineeringSummary(product.product_id)
          window.setTimeout(() => {
            const selectedButton = document.querySelector<HTMLButtonElement>('.studio-product-list button.selected')
            selectedButton?.click()
          }, 80)
        }
        return
      }

      if (button.classList.contains('studio-brand') || text === 'Products') {
        surfaceRef.current = 'control'
        setSurface('control')
      }
    }

    document.addEventListener('click', onClick, true)
    return () => document.removeEventListener('click', onClick, true)
  }, [loadEngineeringSummary, refreshProduct, summary, updateBinding])

  async function changeModule(value: string) {
    const product = activeProductRef.current
    contextSelectionTouchedRef.current = true
    moduleIdRef.current = value
    setModuleId(value)

    const currentDeliverable = summary?.delivery_deliverables.find(
      (item) => item.deliverable_id === deliverableIdRef.current,
    )
    const keepDeliverable = Boolean(value && currentDeliverable?.module_id === value)
    const nextDeliverable = keepDeliverable ? deliverableIdRef.current : ''
    deliverableIdRef.current = nextDeliverable
    setDeliverableId(nextDeliverable)

    if (!product || !canEdit) return
    await updateBinding(product.product_id, {
      module_id: value || null,
      deliverable_id: nextDeliverable || null,
    })
  }

  async function changeDeliverable(value: string) {
    const product = activeProductRef.current
    if (!product || !canEdit) return

    contextSelectionTouchedRef.current = true

    if (!value) {
      deliverableIdRef.current = ''
      setDeliverableId('')
      await updateBinding(product.product_id, {
        module_id: moduleIdRef.current || null,
        deliverable_id: null,
      })
      return
    }

    if (!moduleIdRef.current) {
      setMessage('Select an intended module before choosing a deliverable.')
      return
    }

    const delivery = summary?.delivery_deliverables.find((item) => item.deliverable_id === value)
    if (!delivery) {
      setMessage('Deliverable context is not available. Refresh Product Control and try again.')
      return
    }

    if (!delivery.module_id) {
      const assigned = await assignDeliverable(product.product_id, value, moduleIdRef.current)
      if (!assigned) return
    } else if (delivery.module_id !== moduleIdRef.current) {
      setMessage(`This deliverable belongs to ${delivery.module_name ?? 'another module'}.`)
      return
    }

    deliverableIdRef.current = value
    setDeliverableId(value)
    await updateBinding(product.product_id, {
      module_id: moduleIdRef.current,
      deliverable_id: value,
    })
  }

  const healthLabel = summary?.sync_health === 'attention'
    ? 'Needs attention'
    : summary?.sync_health === 'active'
      ? 'Active sync'
      : summary?.sync_health === 'ready'
        ? 'Ready'
        : 'Not linked'

  const moduleDelivery = summary?.module_delivery ?? []
  const selectedModuleDelivery = moduleDelivery.find((item) => item.module_id === moduleId)
  const moduleDeliverables = (summary?.delivery_deliverables ?? []).filter((item) => item.module_id === moduleId)
  const unassignedDeliverables = (summary?.delivery_deliverables ?? []).filter((item) => !item.module_id)

  return (
    <div className="inzozi-delivery-shell">
      <section className={`inzozi-sync-bar sync-${summary?.sync_health ?? 'not_linked'}`} aria-label="Inzozi AI-Coding delivery synchronization">
        <div className="inzozi-sync-brand">
          <span className="inzozi-sync-mark">IA</span>
          <div>
            <strong>Inzozi AI-Coding</strong>
            <small>One solution · synchronized delivery</small>
          </div>
        </div>

        <div className="inzozi-sync-flow" aria-label="Synchronized modules">
          <span className={surface === 'control' ? 'active' : ''}>Project Control Center</span>
          <b>↔</b>
          <span className={surface === 'engineering' ? 'active' : ''}>Engineering Space</span>
        </div>

        <div className="inzozi-sync-context">
          {activeProduct ? (
            <>
              <div className="inzozi-sync-product">
                <small>ACTIVE SOLUTION</small>
                <strong>{activeProduct.name}</strong>
              </div>
              <label>
                Module
                <select
                  value={moduleId}
                  onChange={(event) => void changeModule(event.target.value)}
                  disabled={!canEdit || !(activeProduct.modules?.length)}
                >
                  <option value="">
                    {activeProduct.modules?.length ? 'Select module' : 'No intended modules yet'}
                  </option>
                  {(activeProduct.modules ?? []).map((module) => {
                    const delivery = moduleDelivery.find((item) => item.module_id === module.module_id)
                    const suffix = delivery?.deliverable_total
                      ? ` · ${delivery.deliverable_complete}/${delivery.deliverable_total}`
                      : ''
                    return (
                      <option value={module.module_id} key={module.module_id}>
                        {module.name}{suffix}
                      </option>
                    )
                  })}
                </select>
              </label>
              <label>
                Deliverable
                <select
                  value={deliverableId}
                  onChange={(event) => void changeDeliverable(event.target.value)}
                  disabled={!canEdit || !moduleId}
                >
                  <option value="">{moduleId ? 'Select deliverable' : 'Select module first'}</option>
                  {moduleId && moduleDeliverables.length > 0 && (
                    <optgroup label={`${selectedModuleDelivery?.name ?? 'Selected module'} deliverables`}>
                      {moduleDeliverables.map((deliverable) => (
                        <option value={deliverable.deliverable_id} key={deliverable.deliverable_id}>
                          {deliverable.title} · {eventLabel(deliverable.status)}
                        </option>
                      ))}
                    </optgroup>
                  )}
                  {moduleId && unassignedDeliverables.length > 0 && (
                    <optgroup label="Unassigned — selecting will attach to this module">
                      {unassignedDeliverables.map((deliverable) => (
                        <option value={deliverable.deliverable_id} key={deliverable.deliverable_id}>
                          {deliverable.title} · {eventLabel(deliverable.status)}
                        </option>
                      ))}
                    </optgroup>
                  )}
                </select>
              </label>
            </>
          ) : (
            <div className="inzozi-sync-product empty">
              <small>ACTIVE SOLUTION</small>
              <strong>Select a solution in Project Control</strong>
            </div>
          )}
        </div>

        <button
          type="button"
          className="inzozi-sync-status"
          onClick={() => setDetailsOpen((current) => !current)}
          aria-expanded={detailsOpen}
        >
          <span className="inzozi-sync-status-dot" />
          <span><strong>{healthLabel}</strong><small>{summary?.evidence_count ?? 0} evidence events</small></span>
        </button>

        {detailsOpen && (
          <div className="inzozi-sync-details">
            <div className="inzozi-sync-detail-summary">
              <div><span>Repository</span><strong>{shortRepository(summary?.binding?.repository_url)}</strong></div>
              <div><span>Workspace</span><strong>{summary?.binding?.workspace_id?.slice(0, 12) ?? 'Not opened'}</strong></div>
              <div><span>Last activity</span><strong>{timeLabel(summary?.last_activity_at)}</strong></div>
            </div>
            {selectedModuleDelivery && (
              <div className="inzozi-module-context-summary">
                <strong>{selectedModuleDelivery.name}</strong>
                <span>{selectedModuleDelivery.deliverable_complete}/{selectedModuleDelivery.deliverable_total} deliverables complete · {selectedModuleDelivery.progress_percent}%</span>
              </div>
            )}
            <div className="inzozi-sync-events">
              <strong>Recent engineering evidence</strong>
              {summary?.recent_events.length ? summary.recent_events.slice(0, 5).map((item) => (
                <article className={`event-${item.status}`} key={item.event_id}>
                  <span>{eventLabel(item.event_type)}</span>
                  <p>{item.summary}</p>
                  <small>{timeLabel(item.created_at)}</small>
                </article>
              )) : <p>No engineering evidence recorded yet. Start from Project Control and continue the selected work in Engineering Space.</p>}
            </div>
          </div>
        )}

        {message && <div className="inzozi-sync-warning" role="status">{message}</div>}
      </section>

      <div className="inzozi-delivery-body">{children}</div>
    </div>
  )
}
