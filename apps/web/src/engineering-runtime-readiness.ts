export {}

type EngineeringReadiness = {
  github_app_configured?: boolean
  workspace_ownership_enforced?: boolean
}

const upstreamFetch = window.fetch.bind(window)
let readiness: EngineeringReadiness | null = null
let renderQueued = false

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

function repositoryLabel(value: string) {
  return value
    .trim()
    .replace(/^https:\/\/github\.com\//, '')
    .replace(/\.git$/, '')
}

function repositoryInput() {
  return document.querySelector<HTMLInputElement>('#repository-url')
}

function noWorkspaceIsActive() {
  const context = document.querySelector<HTMLElement>('.agent-output .context-card strong')
  return !context || context.textContent?.trim() === 'No workspace'
}

function renderGitHubReadiness() {
  const placeholder = document.querySelector<HTMLElement>('.github-selector-placeholder')
  if (!placeholder) return

  const description = placeholder.querySelector<HTMLElement>('small')
  const status = placeholder.querySelector<HTMLButtonElement>('button')
  const configured = readiness?.github_app_configured

  if (configured === true) {
    if (description) {
      description.textContent = 'GitHub App authentication is active. Solution-bound repositories can open guarded workspaces.'
    }
    if (status) {
      status.textContent = 'GitHub App connected'
      status.dataset.state = 'connected'
    }
    return
  }

  if (configured === false) {
    if (description) {
      description.textContent = 'GitHub App authentication is not configured for this environment.'
    }
    if (status) {
      status.textContent = 'GitHub App not configured'
      status.dataset.state = 'disconnected'
    }
    return
  }

  if (description) {
    description.textContent = 'Checking GitHub App readiness…'
  }
  if (status) {
    status.textContent = 'Checking GitHub App…'
    status.dataset.state = 'checking'
  }
}

function renderWorkspaceOwnershipReadiness() {
  const input = repositoryInput()
  const form = input?.closest('form')
  if (!form) return

  const existing = form.querySelector<HTMLElement>('[data-engineering-ownership-readiness]')
  const createButton = form.querySelector<HTMLButtonElement>('button.primary-action')

  if (readiness?.workspace_ownership_enforced !== false) {
    existing?.remove()
    return
  }

  let notice = existing
  if (!notice) {
    notice = document.createElement('div')
    notice.dataset.engineeringOwnershipReadiness = 'disabled'
    notice.className = 'engineering-ownership-readiness'
    createButton?.before(notice)
  }

  notice.textContent = 'Guarded workspace ownership is not enabled in this environment. Workspace creation remains disabled until durable ownership is enabled.'
  if (createButton) {
    createButton.disabled = true
    createButton.title = 'Enable durable workspace ownership before creating a guarded workspace.'
  }
}

function renderRepositoryAwareAgentState() {
  if (!noWorkspaceIsActive()) return

  const url = repositoryInput()?.value.trim() ?? ''
  const output = document.querySelector<HTMLElement>('.agent-output p')
  const badge = document.querySelector<HTMLElement>('.agent-output .route-badge')

  if (!output || !badge) return

  if (url) {
    const label = repositoryLabel(url)
    output.textContent = `Repository ${label} is bound. Create a guarded workspace to give Inzozi execution context.`
    badge.textContent = 'repository bound · no workspace'
    return
  }

  output.textContent = 'Connect or select a repository to give Inzozi engineering context.'
  badge.textContent = 'no repo context'
}

function render() {
  renderGitHubReadiness()
  renderWorkspaceOwnershipReadiness()
  renderRepositoryAwareAgentState()
}

function queueRender() {
  if (renderQueued) return
  renderQueued = true
  window.requestAnimationFrame(() => {
    renderQueued = false
    render()
  })
}

async function loadEngineeringReadiness() {
  try {
    const response = await upstreamFetch('/api/v1/engineering-readiness', {
      credentials: 'same-origin',
    })
    if (!response.ok) {
      readiness = {
        github_app_configured: false,
      }
      queueRender()
      return readiness
    }
    readiness = await response.json() as EngineeringReadiness
  } catch {
    readiness = {
      github_app_configured: false,
    }
  }
  queueRender()
  return readiness
}

const readinessPromise = loadEngineeringReadiness()

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = requestUrl(input)
  const method = requestMethod(input, init)

  if (method === 'GET' && /\/api\/v1\/workspaces\/recovery(?:\?|$)/.test(url)) {
    const current = readiness ?? await readinessPromise
    if (current?.workspace_ownership_enforced === false) {
      return new Response(JSON.stringify({ workspaces: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }
  }

  return upstreamFetch(input, init)
}

const observer = new MutationObserver(() => queueRender())
observer.observe(document.documentElement, {
  childList: true,
  subtree: true,
})

document.addEventListener('input', (event) => {
  if (event.target instanceof HTMLInputElement && event.target.id === 'repository-url') {
    queueRender()
  }
})

document.addEventListener('change', (event) => {
  if (event.target instanceof HTMLInputElement && event.target.id === 'repository-url') {
    queueRender()
  }
})

queueRender()
