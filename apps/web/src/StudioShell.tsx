import { FormEvent, useEffect, useMemo, useState } from 'react'
import App from './App'

type StudioView = 'products' | 'engineering'
type WorkStatus = 'planned' | 'in_progress' | 'blocked' | 'complete'

type Session = {
  permissions: string[]
}

type ProductSummary = {
  product_id: string
  name: string
  summary: string
  status: string
  progress_percent: number
  updated_at: string
}

type Blueprint = {
  concept: string
  target_users: string[]
  platforms: string[]
  capabilities: string[]
  constraints: string[]
  blueprint_version: number
}

type ProductComponent = {
  component_id: string
  component_key: string
  kind: string
  name: string
  description: string
  platform?: string | null
  status: WorkStatus
}

type Deliverable = {
  deliverable_id: string
  component_id?: string | null
  sequence: number
  title: string
  description: string
  status: WorkStatus
}

type Dependency = {
  dependency_id: string
  upstream_name: string
  downstream_name: string
  relationship: string
  impact_note: string
}

type ProductSnapshot = ProductSummary & {
  blueprint: Blueprint
  components: ProductComponent[]
  deliverables: Deliverable[]
  dependencies: Dependency[]
}

type ApiError = { detail?: string }

type ProductForm = {
  name: string
  concept: string
  targetUsers: string
  constraints: string
  platforms: string[]
  capabilities: string[]
}

const PLATFORM_OPTIONS = [
  ['web', 'Web Application'],
  ['windows', 'Windows Desktop'],
  ['macos', 'macOS Desktop'],
  ['android_phone', 'Android Phone'],
  ['ios_phone', 'iPhone'],
  ['android_tablet', 'Android Tablet'],
  ['ipad', 'iPad'],
  ['wearable', 'Wearable / Smart Watch'],
] as const

const CAPABILITY_OPTIONS = [
  ['database', 'Database', 'Store and manage durable product data.'],
  ['internal_api', 'Internal APIs', 'Shared services for web, mobile and other applications.'],
  ['external_integrations', 'External APIs & Integrations', 'Connect payments, SMS, partners or other systems.'],
  ['reporting', 'Reporting & Analytics', 'Dashboards, reports, exports and management information.'],
  ['notifications', 'Notifications', 'Email, SMS, push or in-product alerts.'],
  ['offline', 'Offline Capability', 'Allow selected applications to work without continuous internet.'],
  ['documentation', 'Documentation & Training', 'Maintain manuals, guides and training material.'],
  ['monitoring', 'Operations & Monitoring', 'Health, backups, alerts and operational readiness.'],
] as const

const DEFAULT_FORM: ProductForm = {
  name: '',
  concept: '',
  targetUsers: '',
  constraints: '',
  platforms: ['web'],
  capabilities: ['database', 'internal_api', 'documentation', 'monitoring'],
}

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin', ...init })
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as ApiError
      if (body.detail) detail = body.detail
    } catch {
      // Keep HTTP fallback.
    }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

function splitValues(value: string) {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function pretty(value: string) {
  return value
    .replaceAll('_', ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase())
}

function platformLabel(value: string) {
  return PLATFORM_OPTIONS.find(([option]) => option === value)?.[1] ?? pretty(value)
}

function updatedLabel(value: string) {
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

function toggleValue(values: string[], value: string) {
  return values.includes(value) ? values.filter((item) => item !== value) : [...values, value]
}

function ProductCreatePanel({
  onCreated,
  onCancel,
}: {
  onCreated: (product: ProductSnapshot) => void
  onCancel: () => void
}) {
  const [form, setForm] = useState<ProductForm>(DEFAULT_FORM)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!form.name.trim() || form.concept.trim().length < 20 || !form.platforms.length) return
    setBusy(true)
    setMessage('')
    try {
      const product = await api<ProductSnapshot>('/api/v1/products', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: form.name.trim(),
          concept: form.concept.trim(),
          target_users: splitValues(form.targetUsers),
          constraints: splitValues(form.constraints),
          platforms: form.platforms,
          capabilities: form.capabilities,
        }),
      })
      onCreated(product)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to create product.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="studio-create-card">
      <div className="studio-section-heading">
        <div>
          <span className="studio-eyebrow">GUIDED DISCOVERY</span>
          <h2>Tell Aquila what you want to build.</h2>
          <p>No repository or programming language is required at this stage. Aquila will turn these choices into the first system plan.</p>
        </div>
        <button type="button" className="studio-ghost" onClick={onCancel}>Cancel</button>
      </div>

      <form onSubmit={submit} className="studio-discovery-form">
        <div className="studio-form-section">
          <span className="studio-step">1</span>
          <div>
            <h3>Product idea</h3>
            <p>Describe the problem and desired outcome in ordinary language.</p>
          </div>
          <label>
            Product / solution name
            <input
              value={form.name}
              onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              placeholder="e.g. Hospital Management System"
              required
            />
          </label>
          <label>
            What should this solution do?
            <textarea
              value={form.concept}
              onChange={(event) => setForm((current) => ({ ...current, concept: event.target.value }))}
              placeholder="Describe the business problem, users and the outcome you expect..."
              rows={6}
              required
            />
            <small>{form.concept.trim().length}/20 minimum characters</small>
          </label>
          <label>
            Who will use it? <span>optional</span>
            <input
              value={form.targetUsers}
              onChange={(event) => setForm((current) => ({ ...current, targetUsers: event.target.value }))}
              placeholder="Administrator, customer, field officer, manager"
            />
          </label>
        </div>

        <div className="studio-form-section">
          <span className="studio-step">2</span>
          <div>
            <h3>Where should people use it?</h3>
            <p>Select every user experience that matters. Aquila will plan and monitor each one separately.</p>
          </div>
          <div className="studio-choice-grid">
            {PLATFORM_OPTIONS.map(([value, label]) => (
              <label className={`studio-choice ${form.platforms.includes(value) ? 'selected' : ''}`} key={value}>
                <input
                  type="checkbox"
                  checked={form.platforms.includes(value)}
                  onChange={() => setForm((current) => ({
                    ...current,
                    platforms: toggleValue(current.platforms, value),
                  }))}
                />
                <strong>{label}</strong>
              </label>
            ))}
          </div>
        </div>

        <div className="studio-form-section">
          <span className="studio-step">3</span>
          <div>
            <h3>What supporting capabilities are relevant?</h3>
            <p>These become part of the product plan and later change-impact analysis.</p>
          </div>
          <div className="studio-capability-grid">
            {CAPABILITY_OPTIONS.map(([value, label, description]) => (
              <label className={`studio-capability ${form.capabilities.includes(value) ? 'selected' : ''}`} key={value}>
                <input
                  type="checkbox"
                  checked={form.capabilities.includes(value)}
                  onChange={() => setForm((current) => ({
                    ...current,
                    capabilities: toggleValue(current.capabilities, value),
                  }))}
                />
                <span><strong>{label}</strong><small>{description}</small></span>
              </label>
            ))}
          </div>
        </div>

        <div className="studio-form-section">
          <span className="studio-step">4</span>
          <div>
            <h3>Known constraints</h3>
            <p>Capture important boundaries now; they remain part of the Product Blueprint.</p>
          </div>
          <label>
            Constraints <span>optional, comma or new line separated</span>
            <textarea
              value={form.constraints}
              onChange={(event) => setForm((current) => ({ ...current, constraints: event.target.value }))}
              placeholder="Must work offline in rural areas\nMust support English and Kinyarwanda"
              rows={4}
            />
          </label>
        </div>

        {message && <div className="studio-error" role="alert">{message}</div>}
        <div className="studio-create-actions">
          <div>
            <strong>Aquila will create:</strong>
            <span>Blueprint · System components · Delivery sequence · Initial dependency map</span>
          </div>
          <button
            className="studio-primary"
            disabled={busy || form.concept.trim().length < 20 || !form.name.trim() || !form.platforms.length}
          >
            {busy ? 'Creating product plan…' : 'Create Product Plan'}
          </button>
        </div>
      </form>
    </section>
  )
}

function ProjectControlCenter({
  product,
  canEdit,
  savingDeliverableId,
  onStatusChange,
  onOpenEngineering,
}: {
  product: ProductSnapshot
  canEdit: boolean
  savingDeliverableId: string | null
  onStatusChange: (deliverable: Deliverable, status: WorkStatus) => Promise<void>
  onOpenEngineering: () => void
}) {
  const grouped = useMemo(() => {
    const groups = new Map<string, ProductComponent[]>()
    for (const component of product.components) {
      const current = groups.get(component.kind) ?? []
      current.push(component)
      groups.set(component.kind, current)
    }
    return Array.from(groups.entries())
  }, [product.components])

  const dependencyGroups = useMemo(() => {
    const groups = new Map<string, {
      upstreamName: string
      relationship: string
      downstreamNames: string[]
    }>()

    for (const dependency of product.dependencies) {
      const key = `${dependency.upstream_name}::${dependency.relationship}`
      const existing = groups.get(key)
      if (existing) {
        if (!existing.downstreamNames.includes(dependency.downstream_name)) {
          existing.downstreamNames.push(dependency.downstream_name)
        }
        continue
      }
      groups.set(key, {
        upstreamName: dependency.upstream_name,
        relationship: dependency.relationship,
        downstreamNames: [dependency.downstream_name],
      })
    }

    return Array.from(groups.values())
  }, [product.dependencies])

  const completed = product.deliverables.filter((item) => item.status === 'complete').length
  const blocked = product.deliverables.filter((item) => item.status === 'blocked').length
  const current = product.deliverables.find((item) => item.status === 'in_progress')
    ?? product.deliverables.find((item) => item.status === 'planned')

  return (
    <div className="studio-control-center">
      <section className="studio-product-hero">
        <div>
          <span className="studio-eyebrow">PROJECT CONTROL CENTER</span>
          <h1>{product.name}</h1>
          <p>{product.summary}</p>
          <div className="studio-tags">
            {product.blueprint.platforms.map((item) => <span key={item}>{platformLabel(item)}</span>)}
          </div>
        </div>
        <div className="studio-hero-actions">
          <button className="studio-primary" onClick={onOpenEngineering}>Open Engineering Workspace</button>
          <small>Advanced code, repository, tests and Git controls</small>
        </div>
      </section>

      <section className="studio-metrics">
        <article><span>Overall progress</span><strong>{product.progress_percent}%</strong><small>{completed}/{product.deliverables.length} deliverables complete</small></article>
        <article><span>System components</span><strong>{product.components.length}</strong><small>Tracked independently</small></article>
        <article><span>Current focus</span><strong className="metric-text">{current?.title ?? 'Planning complete'}</strong><small>Next in delivery sequence</small></article>
        <article><span>Blocked</span><strong>{blocked}</strong><small>{blocked ? 'Needs attention' : 'No blocked deliverables'}</small></article>
      </section>

      <section className="studio-progress-card">
        <div><strong>Delivery progress</strong><span>{product.progress_percent}%</span></div>
        <div className="studio-progress-track"><span style={{ width: `${product.progress_percent}%` }} /></div>
      </section>

      <div className="studio-control-grid">
        <section className="studio-card studio-roadmap">
          <div className="studio-card-heading">
            <div><span className="studio-eyebrow">SEQUENCE OF WORK</span><h2>Deliverables</h2></div>
            <small>Updated throughout development</small>
          </div>
          <div className="studio-deliverables">
            {product.deliverables.map((item) => (
              <article className="studio-deliverable" key={item.deliverable_id}>
                <span className={`studio-status-dot status-${item.status}`} />
                <div className="studio-deliverable-number">{item.sequence}</div>
                <div className="studio-deliverable-copy">
                  <strong>{item.title}</strong>
                  <p>{item.description}</p>
                </div>
                {canEdit ? (
                  <div className="studio-status-control">
                    <select
                      value={item.status}
                      disabled={Boolean(savingDeliverableId)}
                      aria-busy={savingDeliverableId === item.deliverable_id}
                      onChange={(event) => void onStatusChange(item, event.target.value as WorkStatus)}
                      aria-label={`Status for ${item.title}`}
                    >
                      <option value="planned">Planned</option>
                      <option value="in_progress">In progress</option>
                      <option value="blocked">Blocked</option>
                      <option value="complete">Complete</option>
                    </select>
                    {savingDeliverableId === item.deliverable_id && <small role="status">Saving…</small>}
                  </div>
                ) : <span className={`studio-status-label status-${item.status}`}>{pretty(item.status)}</span>}
              </article>
            ))}
          </div>
        </section>

        <aside className="studio-side-stack">
          <section className="studio-card">
            <div className="studio-card-heading"><div><span className="studio-eyebrow">PRODUCT BLUEPRINT</span><h2>Scope</h2></div><small>v{product.blueprint.blueprint_version}</small></div>
            <p className="studio-blueprint-concept">{product.blueprint.concept}</p>
            <dl className="studio-blueprint-list">
              <div><dt>Target users</dt><dd>{product.blueprint.target_users.length ? product.blueprint.target_users.join(', ') : 'To be refined'}</dd></div>
              <div><dt>Platforms</dt><dd>{product.blueprint.platforms.map(platformLabel).join(', ')}</dd></div>
              <div><dt>Capabilities</dt><dd>{product.blueprint.capabilities.map(pretty).join(', ')}</dd></div>
              {product.blueprint.constraints.length > 0 && <div><dt>Constraints</dt><dd>{product.blueprint.constraints.join('; ')}</dd></div>}
            </dl>
          </section>

          <section className="studio-card">
            <div className="studio-card-heading"><div><span className="studio-eyebrow">CHANGE SAFETY</span><h2>Dependencies</h2></div><small>{product.dependencies.length} mapped</small></div>
            <div className="studio-dependencies studio-dependency-groups">
              {dependencyGroups.slice(0, 5).map((group) => (
                <article className="studio-dependency-group" key={`${group.upstreamName}:${group.relationship}`}>
                  <div className="studio-dependency-group-heading">
                    <strong>{group.upstreamName}</strong>
                    <span>→ {group.relationship}</span>
                  </div>
                  <p>{group.downstreamNames.join(', ')}</p>
                </article>
              ))}
              {dependencyGroups.length > 5 && (
                <small>+ {dependencyGroups.length - 5} additional dependency groups · {product.dependencies.length} relationships total</small>
              )}
            </div>
          </section>
        </aside>
      </div>

      <section className="studio-card studio-system-map">
        <div className="studio-card-heading">
          <div><span className="studio-eyebrow">SYSTEM MAP</span><h2>Product components</h2></div>
          <small>Each component can evolve independently with impact review</small>
        </div>
        <div className="studio-component-groups">
          {grouped.map(([kind, components]) => (
            <div className="studio-component-group" key={kind}>
              <span>{pretty(kind)}</span>
              {components.map((component) => (
                <article key={component.component_id}>
                  <strong>{component.name}</strong>
                  <p>{component.description}</p>
                  <small>{pretty(component.status)}</small>
                </article>
              ))}
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

export default function StudioShell() {
  const [view, setView] = useState<StudioView>('products')
  const [session, setSession] = useState<Session | null>(null)
  const [products, setProducts] = useState<ProductSummary[]>([])
  const [selected, setSelected] = useState<ProductSnapshot | null>(null)
  const [creating, setCreating] = useState(false)
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')
  const [savingDeliverableId, setSavingDeliverableId] = useState<string | null>(null)

  const canEdit = Boolean(session?.permissions.includes('workspace:edit'))

  async function loadProducts() {
    setLoading(true)
    setMessage('')
    try {
      const [sessionPayload, payload] = await Promise.all([
        api<Session>('/api/v1/auth/me'),
        api<{ products: ProductSummary[] }>('/api/v1/products'),
      ])
      setSession(sessionPayload)
      setProducts(payload.products)
      if (selected) {
        const refreshed = await api<ProductSnapshot>(`/api/v1/products/${selected.product_id}`)
        setSelected(refreshed)
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to load Aquila Studio products.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadProducts()
  }, [])

  async function openProduct(productId: string) {
    setMessage('')
    try {
      setSelected(await api<ProductSnapshot>(`/api/v1/products/${productId}`))
      setCreating(false)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to open product.')
    }
  }

  function productCreated(product: ProductSnapshot) {
    setProducts((current) => [product, ...current.filter((item) => item.product_id !== product.product_id)])
    setSelected(product)
    setCreating(false)
  }

  async function updateStatus(deliverable: Deliverable, status: WorkStatus) {
    if (!selected || deliverable.status === status || savingDeliverableId) return
    setMessage('')
    setSavingDeliverableId(deliverable.deliverable_id)
    try {
      const refreshed = await api<ProductSnapshot>(
        `/api/v1/products/${selected.product_id}/deliverables/${deliverable.deliverable_id}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ status }),
        },
      )
      setSelected(refreshed)
      setProducts((current) => current.map((item) => (
        item.product_id === refreshed.product_id ? refreshed : item
      )))
    } catch (error) {
      setMessage(error instanceof Error ? error.message : 'Unable to update deliverable.')
    } finally {
      setSavingDeliverableId(null)
    }
  }

  if (view === 'engineering') {
    return (
      <div className="studio-engineering-mode">
        <button className="studio-return-control" onClick={() => setView('products')}>
          ← Project Control Center
        </button>
        <App />
      </div>
    )
  }

  return (
    <main className="studio-shell">
      <header className="studio-topbar">
        <button className="studio-brand" onClick={() => { setSelected(null); setCreating(false) }}>
          <span className="studio-mark">A</span>
          <span><strong>Aquila Studio</strong><small>Plan · Build · Certify · Operate</small></span>
        </button>
        <nav>
          <button className="active" onClick={() => { setSelected(null); setCreating(false) }}>Products</button>
          <button onClick={() => setView('engineering')}>Engineering Workspace</button>
        </nav>
        <div className="studio-aquila-state"><span>●</span> Aquila</div>
      </header>

      <div className="studio-layout">
        <aside className="studio-sidebar">
          <div className="studio-sidebar-heading">
            <span>MY PRODUCTS</span>
            {canEdit && <button onClick={() => { setCreating(true); setSelected(null) }}>＋</button>}
          </div>
          {loading ? <p className="studio-muted">Loading products…</p> : products.length ? (
            <div className="studio-product-list">
              {products.map((product) => (
                <button
                  className={selected?.product_id === product.product_id ? 'selected' : ''}
                  key={product.product_id}
                  onClick={() => void openProduct(product.product_id)}
                >
                  <strong>{product.name}</strong>
                  <span>{product.progress_percent}% complete</span>
                  <small>Updated {updatedLabel(product.updated_at)}</small>
                </button>
              ))}
            </div>
          ) : <p className="studio-muted">No products yet. Start from an idea, not a repository.</p>}

          <div className="studio-sidebar-footer">
            <span>ADVANCED</span>
            <button onClick={() => setView('engineering')}>⌘ Engineering Workspace</button>
            <small>Repository, code, tests and Git approvals</small>
          </div>
        </aside>

        <section className="studio-main">
          {message && <div className="studio-global-message" role="alert">{message}</div>}
          {creating ? (
            <ProductCreatePanel onCreated={productCreated} onCancel={() => setCreating(false)} />
          ) : selected ? (
            <ProjectControlCenter
              product={selected}
              canEdit={canEdit}
              savingDeliverableId={savingDeliverableId}
              onStatusChange={updateStatus}
              onOpenEngineering={() => setView('engineering')}
            />
          ) : (
            <section className="studio-welcome">
              <div className="studio-welcome-copy">
                <span className="studio-eyebrow">AI SOFTWARE DELIVERY OPERATING SYSTEM</span>
                <h1>Start with the solution you want—not the code you need.</h1>
                <p>
                  Aquila Studio turns an idea into a product blueprint, system map, ordered deliverables and a managed engineering journey. Technical details remain available when you need them.
                </p>
                <div className="studio-welcome-actions">
                  {canEdit && <button className="studio-primary" onClick={() => setCreating(true)}>Create a New Solution</button>}
                  {products.length > 0 && <button className="studio-secondary" onClick={() => void openProduct(products[0].product_id)}>Continue Latest Product</button>}
                </div>
              </div>
              <div className="studio-lifecycle">
                {['Idea', 'Plan', 'Architecture', 'Build', 'Test', 'Certify', 'Deploy', 'Operate', 'Improve', 'Reuse'].map((item, index) => (
                  <div key={item}><span>{String(index + 1).padStart(2, '0')}</span><strong>{item}</strong></div>
                ))}
              </div>
              <div className="studio-foundation-note">
                <strong>Your existing Engineering Runtime is preserved.</strong>
                <p>Secure workspaces, Aquila modes, code editor, tests, Git approvals, monitoring and rollback now sit underneath Product Control instead of being the starting point.</p>
              </div>
            </section>
          )}
        </section>
      </div>
    </main>
  )
}
