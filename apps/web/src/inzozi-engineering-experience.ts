type RepositoryRecord = {
  repository_id: string
  repository_url: string
  repository_ref?: string | null
  workspace_id?: string | null
  is_default?: boolean
}

type EngineeringSummary = {
  binding?: {
    module_id?: string | null
    deliverable_id?: string | null
    repository_url?: string | null
    workspace_id?: string | null
    repository_ref?: string | null
  } | null
  repositories?: RepositoryRecord[]
  resolved_repository?: RepositoryRecord | null
  repository_resolution?: 'none' | 'product_default' | 'module_override'
}

type ProductSnapshot = {
  product_id: string
  name: string
}

type ApiError = { detail?: string }

const originalFetch = window.fetch.bind(window)
let activeProduct: ProductSnapshot | null = null
let summary: EngineeringSummary | null = null
let renderQueued = false
let autoResumeAttempted = ''

function requestUrl(input: RequestInfo | URL) {
  if (typeof input === 'string') return input
  if (input instanceof URL) return input.toString()
  return input.url
}

function requestMethod(input: RequestInfo | URL, init?: RequestInit) {
  if (init?.method) return init.method.toUpperCase()
  if (typeof Request !== 'undefined' && input instanceof Request) return input.method.toUpperCase()
  return 'GET'
}

function legacyToInzozi(value: string) {
  return value
    .replaceAll('@aquila', '@inzozi')
    .replaceAll('AQUILA', 'INZOZI')
    .replaceAll('Aquila', 'Inzozi')
}

function normalizeLegacyText(root: ParentNode | Node = document) {
  if (root instanceof Text) {
    const current = root.nodeValue ?? ''
    const next = legacyToInzozi(current)
    if (next !== current) root.nodeValue = next
    return
  }

  const parent = root instanceof ParentNode ? root : document
  const walker = document.createTreeWalker(parent, NodeFilter.SHOW_TEXT)
  let node = walker.nextNode()
  while (node) {
    const current = node.nodeValue ?? ''
    const next = legacyToInzozi(current)
    if (next !== current) node.nodeValue = next
    node = walker.nextNode()
  }

  if (root instanceof Element) {
    for (const attribute of ['aria-label', 'title', 'placeholder']) {
      const current = root.getAttribute(attribute)
      if (!current) continue
      const next = legacyToInzozi(current)
      if (next !== current) root.setAttribute(attribute, next)
    }
  }

  if (parent instanceof Document || parent instanceof Element) {
    parent.querySelectorAll('[aria-label], [title], [placeholder]').forEach((element) => {
      for (const attribute of ['aria-label', 'title', 'placeholder']) {
        const current = element.getAttribute(attribute)
        if (!current) continue
        const next = legacyToInzozi(current)
        if (next !== current) element.setAttribute(attribute, next)
      }
    })
  }
}

function emitInput(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
  setter?.call(input, value)
  input.dispatchEvent(new Event('input', { bubbles: true }))
  input.dispatchEvent(new Event('change', { bubbles: true }))
}

function prefillRepositoryForm(repository: RepositoryRecord) {
  const repositoryInput = document.querySelector<HTMLInputElement>('#repository-url')
  if (!repositoryInput) return false
  if (repositoryInput.value !== repository.repository_url) {
    emitInput(repositoryInput, repository.repository_url)
  }
  const form = repositoryInput.closest('form')
  const refInput = form?.querySelector<HTMLInputElement>('input[placeholder="main"]')
  const repositoryRef = repository.repository_ref ?? ''
  if (refInput && refInput.value !== repositoryRef) emitInput(refInput, repositoryRef)
  const message = form?.querySelector<HTMLElement>('.workspace-message')
  if (message) {
    message.textContent = `Repository prepared from ${summary?.repository_resolution === 'module_override' ? 'module override' : 'solution default'}. Open the guarded workspace to continue this deliverable.`
  }
  return true
}

function selectedContext() {
  const selects = Array.from(document.querySelectorAll<HTMLSelectElement>('.inzozi-sync-context select'))
  const moduleSelect = selects[0]
  const deliverableSelect = selects[1]
  return {
    moduleId: summary?.binding?.module_id ?? moduleSelect?.value ?? '',
    moduleLabel: moduleSelect?.selectedOptions[0]?.textContent?.split(' · ')[0]?.trim() ?? 'Selected module',
    deliverableId: summary?.binding?.deliverable_id ?? deliverableSelect?.value ?? '',
    deliverableLabel: deliverableSelect?.selectedOptions[0]?.textContent?.split(' · ')[0]?.trim() ?? 'Selected deliverable',
  }
}

function isEngineeringSurface() {
  return Array.from(document.querySelectorAll('.inzozi-sync-flow span.active'))
    .some((item) => item.textContent?.includes('Engineering Space'))
}

async function responseJson<T>(response: Response): Promise<T | null> {
  try {
    return await response.clone().json() as T
  } catch {
    return null
  }
}

async function refreshEngineeringSummary() {
  if (!activeProduct) return
  const response = await originalFetch(`/api/v1/products/${activeProduct.product_id}/engineering`, {
    credentials: 'same-origin',
  })
  if (!response.ok) return
  summary = await response.json() as EngineeringSummary
  queueRender()
}

async function saveRepository(repositoryUrl: string, repositoryRef: string, makeDefault: boolean) {
  if (!activeProduct) throw new Error('Select a solution before connecting an engineering repository.')
  const response = await originalFetch(`/api/v1/products/${activeProduct.product_id}/engineering/repositories`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      repository_url: repositoryUrl,
      repository_ref: repositoryRef || null,
      make_default: makeDefault,
    }),
  })
  if (!response.ok) {
    const body = await responseJson<ApiError>(response)
    throw new Error(body?.detail ?? `Unable to save repository (${response.status})`)
  }
  await refreshEngineeringSummary()
}

async function bindRepositoryToModule(repositoryId: string | null) {
  if (!activeProduct) throw new Error('Select a solution first.')
  const { moduleId } = selectedContext()
  if (!moduleId) throw new Error('Select an intended module first.')
  const response = await originalFetch(
    `/api/v1/products/${activeProduct.product_id}/engineering/modules/${moduleId}/repository`,
    {
      method: 'PUT',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repository_id: repositoryId }),
    },
  )
  if (!response.ok) {
    const body = await responseJson<ApiError>(response)
    throw new Error(body?.detail ?? `Unable to bind repository (${response.status})`)
  }
  await refreshEngineeringSummary()
}

function statusMessage(card: HTMLElement, value: string, isError = false) {
  const target = card.querySelector<HTMLElement>('[data-inzozi-repository-status]')
  if (!target) return
  target.textContent = value
  target.dataset.state = isError ? 'error' : 'ok'
}

function repositoryLabel(url: string) {
  return url.replace(/^https:\/\/github\.com\//, '').replace(/\.git$/, '')
}

function buildRepositoryCard() {
  const context = selectedContext()
  const repositories = summary?.repositories ?? []
  const resolved = summary?.resolved_repository ?? null
  const card = document.createElement('section')
  card.id = 'inzozi-engineering-binding-card'
  card.className = 'inzozi-engineering-binding-card'

  const options = repositories.map((repository) => (
    `<option value="${repository.repository_id}"${resolved?.repository_id === repository.repository_id ? ' selected' : ''}>${repositoryLabel(repository.repository_url)}${repository.is_default ? ' · solution default' : ''}</option>`
  )).join('')

  card.innerHTML = `
    <div class="inzozi-repository-heading">
      <div>
        <span>ENGINEERING CONTEXT</span>
        <strong>${context.moduleLabel} is ready for engineering</strong>
        <small>${context.deliverableLabel}</small>
      </div>
      <span class="inzozi-repository-resolution">${resolved ? (summary?.repository_resolution === 'module_override' ? 'Module repository' : 'Solution default') : 'Repository required'}</span>
    </div>
    <p>${resolved
      ? `Inzozi will use <strong>${repositoryLabel(resolved.repository_url)}</strong> for this work item and keep engineering evidence synchronized back to Project Control.`
      : 'Connect the repository that contains this solution. Inzozi will remember it and keep code, tests and delivery evidence synchronized with this work item.'}
    </p>
    ${repositories.length ? `
      <div class="inzozi-existing-repository-row">
        <label>Repository for this module
          <select data-inzozi-existing-repository>
            <option value="">Use solution default</option>
            ${options}
          </select>
        </label>
        <button type="button" data-inzozi-bind-module>Use for this module</button>
      </div>
    ` : ''}
    <form data-inzozi-add-repository>
      <label>Repository HTTPS URL
        <input type="url" required placeholder="https://github.com/owner/repo" value="${resolved?.repository_url ?? ''}">
      </label>
      <label>Branch / ref <span>optional</span>
        <input type="text" placeholder="main" value="${resolved?.repository_ref ?? ''}">
      </label>
      <label class="inzozi-default-check"><input type="checkbox" ${repositories.some((item) => item.is_default) ? '' : 'checked'}> Use as solution default</label>
      <div class="inzozi-repository-actions">
        <button type="submit" class="primary-action">${resolved ? 'Save repository settings' : 'Connect engineering repository'}</button>
        ${resolved ? '<button type="button" data-inzozi-prepare>Prepare workspace form</button>' : ''}
        <button type="button" data-inzozi-return>Return to Project Control</button>
      </div>
      <small data-inzozi-repository-status>${resolved?.workspace_id ? `Last workspace ${resolved.workspace_id.slice(0, 8)} is remembered.` : resolved ? 'Repository is bound. Open a guarded workspace to begin engineering.' : 'No repository is bound yet.'}</small>
    </form>
  `

  card.querySelector<HTMLFormElement>('[data-inzozi-add-repository]')?.addEventListener('submit', (event) => {
    event.preventDefault()
    const form = event.currentTarget as HTMLFormElement
    const inputs = form.querySelectorAll<HTMLInputElement>('input')
    const url = inputs[0]?.value.trim() ?? ''
    const ref = inputs[1]?.value.trim() ?? ''
    const makeDefault = Boolean(inputs[2]?.checked)
    statusMessage(card, 'Saving repository…')
    void saveRepository(url, ref, makeDefault)
      .then(() => statusMessage(card, 'Repository saved. Inzozi has prepared the workspace form.'))
      .catch((error) => statusMessage(card, error instanceof Error ? error.message : 'Unable to save repository.', true))
  })

  card.querySelector<HTMLButtonElement>('[data-inzozi-bind-module]')?.addEventListener('click', () => {
    const select = card.querySelector<HTMLSelectElement>('[data-inzozi-existing-repository]')
    const repositoryId = select?.value || null
    statusMessage(card, 'Updating module repository…')
    void bindRepositoryToModule(repositoryId)
      .then(() => statusMessage(card, repositoryId ? 'Module repository override saved.' : 'Module now inherits the solution default repository.'))
      .catch((error) => statusMessage(card, error instanceof Error ? error.message : 'Unable to bind module repository.', true))
  })

  card.querySelector<HTMLButtonElement>('[data-inzozi-prepare]')?.addEventListener('click', () => {
    if (resolved && prefillRepositoryForm(resolved)) statusMessage(card, 'Workspace form prepared below.')
  })

  card.querySelector<HTMLButtonElement>('[data-inzozi-return]')?.addEventListener('click', () => {
    const button = Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
      .find((item) => item.textContent?.includes('Project Control Center'))
    button?.click()
  })

  return card
}

function renderRepositoryExperience() {
  normalizeLegacyText(document)
  const existing = document.querySelector('#inzozi-engineering-binding-card')
  if (!isEngineeringSurface() || !activeProduct || !summary?.binding?.module_id || !summary?.binding?.deliverable_id) {
    existing?.remove()
    return
  }

  const welcomeCard = document.querySelector<HTMLElement>('.empty-editor.first-run .welcome-card')
  if (!welcomeCard) {
    existing?.remove()
    return
  }

  const next = buildRepositoryCard()
  if (existing) existing.replaceWith(next)
  else welcomeCard.prepend(next)
  welcomeCard.classList.add('inzozi-contextual-engineering')

  const resolved = summary.resolved_repository
  if (resolved) {
    prefillRepositoryForm(resolved)
    if (resolved.workspace_id && autoResumeAttempted !== resolved.workspace_id) {
      const prefix = resolved.workspace_id.slice(0, 8)
      const row = Array.from(document.querySelectorAll<HTMLElement>('.recoverable-workspace-row'))
        .find((item) => item.textContent?.includes(prefix))
      const resume = row?.querySelector<HTMLButtonElement>('.recoverable-workspace-resume')
      if (resume && !resume.disabled) {
        autoResumeAttempted = resolved.workspace_id
        window.setTimeout(() => resume.click(), 100)
      }
    }
  }
}

function queueRender() {
  if (renderQueued) return
  renderQueued = true
  window.requestAnimationFrame(() => {
    renderQueued = false
    renderRepositoryExperience()
  })
}

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const response = await originalFetch(input, init)
  const url = requestUrl(input)
  const method = requestMethod(input, init)

  if (response.ok && method === 'GET' && /\/api\/v1\/products\/[^/?]+(?:\?|$)/.test(url) && !url.includes('/engineering')) {
    void responseJson<ProductSnapshot>(response).then((payload) => {
      if (!payload?.product_id) return
      activeProduct = payload
      queueRender()
    })
  }

  if (response.ok && url.includes('/api/v1/products/') && url.includes('/engineering')) {
    void responseJson<EngineeringSummary>(response).then((payload) => {
      if (!payload) return
      if (Array.isArray(payload.repositories) || 'resolved_repository' in payload) {
        summary = payload
        queueRender()
      }
    })
  }

  return response
}

const observer = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    if (mutation.type === 'characterData' && mutation.target instanceof Text) {
      normalizeLegacyText(mutation.target)
    } else {
      mutation.addedNodes.forEach((node) => normalizeLegacyText(node))
    }
  }
  queueRender()
})

function start() {
  normalizeLegacyText(document)
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    characterData: true,
    attributes: true,
    attributeFilter: ['aria-label', 'title', 'placeholder'],
  })
  queueRender()
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true })
else start()
