type WorkspaceRuntimeStatus = {
  github_app_configured?: boolean
}

let githubAppConfigured: boolean | null = null
let renderQueued = false

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

  if (githubAppConfigured === true) {
    if (description) {
      description.textContent = 'GitHub App authentication is active. Solution-bound repositories can open guarded workspaces.'
    }
    if (status) {
      status.textContent = 'GitHub App connected'
      status.dataset.state = 'connected'
    }
    return
  }

  if (githubAppConfigured === false) {
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

async function loadRuntimeReadiness() {
  try {
    const response = await window.fetch('/api/v1/workspaces/runtime', {
      credentials: 'same-origin',
    })
    if (!response.ok) {
      githubAppConfigured = false
      queueRender()
      return
    }
    const payload = await response.json() as WorkspaceRuntimeStatus
    githubAppConfigured = payload.github_app_configured === true
  } catch {
    githubAppConfigured = false
  }
  queueRender()
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

void loadRuntimeReadiness()
queueRender()
