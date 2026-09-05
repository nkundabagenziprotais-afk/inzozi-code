import Editor from '@monaco-editor/react'
import { FormEvent, useEffect, useMemo, useState } from 'react'

type Mode = 'ask' | 'plan' | 'design' | 'build' | 'debug' | 'review' | 'deploy'
type BottomTab = 'terminal' | 'status' | 'diff' | 'review'
type TreeEntry = { name: string; type: 'directory' | 'file' }
type FilePayload = { path: string; content: string; sha256: string }
type CommandResult = { exit_code: number; output: string; timed_out: boolean; action?: string }
type ProviderItem = {
  alias: string
  mention: string
  display_name: string
  provider: string
  kind: string
  transport: string
  implemented: boolean
  configured: boolean
  model?: string | null
  capabilities: string[]
}
type ProjectPolicy = {
  primary_provider: string
  design_provider: string
  review_provider: string
  max_specialists: number
}
type AgentRun = {
  status: string
  provider_alias: string
  provider: string
  model: string
  message: string
  requested_providers: string[]
  specialists: string[]
  unknown_mentions: string[]
  notices: string[]
  checkpoint_id?: string | null
  git_diff?: string
}
type GitReview = {
  branch: string
  head: string
  status: string
  diff: string
  fingerprint: string
  protected_branch: boolean
  changed_paths: string[]
  dirty: boolean
}
type CommitApproval = {
  approval_id: string
  branch: string
  head: string
  message: string
  status: string
  diff: string
  changed_paths: string[]
  tree_hash: string
  expires_at: string
  requires_human_approval: boolean
}
type CommitResult = {
  status: string
  branch: string
  commit_sha: string
  tree_hash: string
  message: string
  pushed: boolean
  remote_write_enabled: boolean
}
type ApiError = { detail?: string }

const DEFAULT_PROJECT_POLICY: ProjectPolicy = {
  primary_provider: 'aquila',
  design_provider: 'aquila',
  review_provider: 'chatgpt',
  max_specialists: 2,
}

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const payload = (await response.json()) as ApiError
      if (typeof payload.detail === 'string') detail = payload.detail
    } catch {
      // Keep the HTTP fallback message.
    }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

function joinPath(base: string, name: string) {
  return base ? `${base}/${name}` : name
}

function parentPath(path: string) {
  const parts = path.split('/').filter(Boolean)
  parts.pop()
  return parts.join('/')
}

function languageFromPath(path: string) {
  const extension = path.split('.').pop()?.toLowerCase()
  const languages: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    php: 'php', py: 'python', json: 'json', md: 'markdown', css: 'css',
    html: 'html', yml: 'yaml', yaml: 'yaml', sh: 'shell', sql: 'sql',
    xml: 'xml', vue: 'html', dart: 'dart', go: 'go', rs: 'rust',
  }
  return languages[extension ?? ''] ?? 'plaintext'
}

function repoLabel(repositoryUrl: string) {
  if (!repositoryUrl) return 'No repository connected'
  return repositoryUrl.replace(/^https:\/\/github\.com\//, '').replace(/\.git$/, '')
}

function projectPolicyKey(repositoryUrl: string) {
  return `inzozi-code:ai-policy:${repositoryUrl.trim().toLowerCase().replace(/\.git$/, '')}`
}

function loadProjectPolicy(repositoryUrl: string): ProjectPolicy {
  if (!repositoryUrl || typeof window === 'undefined') return DEFAULT_PROJECT_POLICY
  try {
    const stored = window.localStorage.getItem(projectPolicyKey(repositoryUrl))
    if (!stored) return DEFAULT_PROJECT_POLICY
    return { ...DEFAULT_PROJECT_POLICY, ...(JSON.parse(stored) as Partial<ProjectPolicy>) }
  } catch {
    return DEFAULT_PROJECT_POLICY
  }
}

function approvalTime(value: string) {
  try {
    return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch {
    return value
  }
}

export default function App() {
  const [mode, setMode] = useState<Mode>('plan')
  const [prompt, setPrompt] = useState('Review this project and propose the safest implementation plan.')
  const [agentMessage, setAgentMessage] = useState('Connect a repository to give Aquila a real workspace context.')
  const [agentRoute, setAgentRoute] = useState('@aquila · safe mode')
  const [providers, setProviders] = useState<ProviderItem[]>([])
  const [projectPolicy, setProjectPolicy] = useState<ProjectPolicy>(DEFAULT_PROJECT_POLICY)

  const [repositoryUrl, setRepositoryUrl] = useState('')
  const [repositoryRef, setRepositoryRef] = useState('')
  const [workspaceId, setWorkspaceId] = useState('')
  const [treePath, setTreePath] = useState('')
  const [entries, setEntries] = useState<TreeEntry[]>([])
  const [selectedPath, setSelectedPath] = useState('')
  const [fileContent, setFileContent] = useState('')
  const [fileSha, setFileSha] = useState('')
  const [savedContent, setSavedContent] = useState('')

  const [bottomTab, setBottomTab] = useState<BottomTab>('terminal')
  const [terminalOutput, setTerminalOutput] = useState('Workspace commands will appear here.')
  const [gitStatus, setGitStatus] = useState('No workspace connected.')
  const [gitDiff, setGitDiff] = useState('No diff available.')
  const [gitReview, setGitReview] = useState<GitReview | null>(null)
  const [commitApproval, setCommitApproval] = useState<CommitApproval | null>(null)
  const [commitMessage, setCommitMessage] = useState('feat: apply reviewed Inzozi Code change')
  const [branchName, setBranchName] = useState('feature/inzozi-change')
  const [gitReviewMessage, setGitReviewMessage] = useState('Review changes before creating a local commit. Remote push is disabled.')
  const [busy, setBusy] = useState(false)
  const [workspaceMessage, setWorkspaceMessage] = useState('Connect a GitHub repository to a guarded workspace.')

  const dirty = fileContent !== savedContent
  const projectName = useMemo(() => repoLabel(repositoryUrl), [repositoryUrl])

  useEffect(() => {
    api<{ providers: ProviderItem[] }>('/api/v1/agent/providers')
      .then((payload) => setProviders(payload.providers))
      .catch(() => setProviders([]))
  }, [])

  async function loadTree(id: string, path = '') {
    const payload = await api<{ path: string; entries: TreeEntry[] }>(`/api/v1/workspaces/${id}/tree?path=${encodeURIComponent(path)}`)
    setTreePath(payload.path)
    setEntries(payload.entries)
  }

  async function refreshGit(id = workspaceId) {
    if (!id) return
    const [status, diff] = await Promise.all([
      api<CommandResult>(`/api/v1/workspaces/${id}/git/status`),
      api<CommandResult>(`/api/v1/workspaces/${id}/git/diff`),
    ])
    setGitStatus(status.output || 'Working tree clean.')
    setGitDiff(diff.output || 'No uncommitted diff.')
  }

  async function loadGitReview(id = workspaceId) {
    if (!id) return
    const review = await api<GitReview>(`/api/v1/workspaces/${id}/git/review`)
    setGitReview(review)
    setGitStatus(review.status || 'Working tree clean.')
    setGitDiff(review.diff || 'No uncommitted diff.')
    return review
  }

  async function connectRepository(event: FormEvent) {
    event.preventDefault()
    if (!repositoryUrl.trim()) return
    setBusy(true)
    setWorkspaceMessage('Creating an isolated workspace and cloning the repository…')
    try {
      const payload = await api<{ workspace_id: string; status: string }>('/api/v1/workspaces', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repository_url: repositoryUrl.trim(), ref: repositoryRef.trim() || null }),
      })
      setWorkspaceId(payload.workspace_id)
      setProjectPolicy(loadProjectPolicy(repositoryUrl.trim()))
      setBranchName(`feature/inzozi-change-${payload.workspace_id.slice(0, 6)}`)
      setCommitApproval(null)
      await loadTree(payload.workspace_id)
      await refreshGit(payload.workspace_id)
      await loadGitReview(payload.workspace_id)
      setWorkspaceMessage('Workspace ready. Select a file or ask Aquila to inspect the repository.')
      setAgentMessage('Aquila now has guarded repository context. Build and Debug runs create a preflight checkpoint before agent edits.')
    } catch (error) {
      setWorkspaceMessage(error instanceof Error ? error.message : 'Workspace creation failed.')
    } finally {
      setBusy(false)
    }
  }

  async function openEntry(entry: TreeEntry) {
    if (!workspaceId) return
    const path = joinPath(treePath, entry.name)
    setBusy(true)
    try {
      if (entry.type === 'directory') {
        await loadTree(workspaceId, path)
      } else {
        const payload = await api<FilePayload>(`/api/v1/workspaces/${workspaceId}/files/${path.split('/').map(encodeURIComponent).join('/')}`)
        setSelectedPath(payload.path)
        setFileContent(payload.content)
        setSavedContent(payload.content)
        setFileSha(payload.sha256)
      }
    } catch (error) {
      setWorkspaceMessage(error instanceof Error ? error.message : 'Unable to open item.')
    } finally {
      setBusy(false)
    }
  }

  async function saveFile() {
    if (!workspaceId || !selectedPath || !dirty) return
    setBusy(true)
    try {
      const payload = await api<{ sha256: string }>(`/api/v1/workspaces/${workspaceId}/files/${selectedPath.split('/').map(encodeURIComponent).join('/')}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: fileContent, expected_sha256: fileSha }),
      })
      setFileSha(payload.sha256)
      setSavedContent(fileContent)
      setCommitApproval(null)
      setGitReviewMessage('Working tree changed. Prepare a fresh commit review before approval.')
      setWorkspaceMessage(`Saved ${selectedPath}`)
      await refreshGit()
      await loadGitReview()
    } catch (error) {
      setWorkspaceMessage(error instanceof Error ? error.message : 'Save failed.')
    } finally {
      setBusy(false)
    }
  }

  async function runAction(action: string) {
    if (!workspaceId) return
    setBusy(true)
    setBottomTab('terminal')
    setTerminalOutput(`Running ${action}…`)
    setCommitApproval(null)
    try {
      const result = await api<CommandResult>(`/api/v1/workspaces/${workspaceId}/actions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      })
      setTerminalOutput(`${action}\n\n${result.output || '(no output)'}\n\nexit code: ${result.exit_code}${result.timed_out ? ' · timed out' : ''}`)
      await refreshGit()
      await loadGitReview()
    } catch (error) {
      setTerminalOutput(error instanceof Error ? error.message : 'Command failed.')
    } finally {
      setBusy(false)
    }
  }

  async function createSafeBranch(event: FormEvent) {
    event.preventDefault()
    if (!workspaceId || !branchName.trim()) return
    setBusy(true)
    try {
      const result = await api<{ branch: string; status: string }>(`/api/v1/workspaces/${workspaceId}/git/branches`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ branch_name: branchName.trim() }),
      })
      setRepositoryRef(result.branch)
      setCommitApproval(null)
      setGitReviewMessage(`Safe local branch created: ${result.branch}`)
      await loadGitReview()
      await refreshGit()
    } catch (error) {
      setGitReviewMessage(error instanceof Error ? error.message : 'Unable to create branch.')
    } finally {
      setBusy(false)
    }
  }

  async function prepareCommit(event: FormEvent) {
    event.preventDefault()
    if (!workspaceId || !commitMessage.trim()) return
    setBusy(true)
    try {
      const approval = await api<CommitApproval>(`/api/v1/workspaces/${workspaceId}/git/commit/prepare`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: commitMessage.trim() }),
      })
      setCommitApproval(approval)
      setGitDiff(approval.diff || 'No diff available.')
      setGitReviewMessage(`Review prepared. Approval expires at ${approvalTime(approval.expires_at)}.`)
      setBottomTab('review')
    } catch (error) {
      setCommitApproval(null)
      setGitReviewMessage(error instanceof Error ? error.message : 'Unable to prepare commit review.')
    } finally {
      setBusy(false)
    }
  }

  async function approveCommit() {
    if (!workspaceId || !commitApproval) return
    setBusy(true)
    try {
      const result = await api<CommitResult>(`/api/v1/workspaces/${workspaceId}/git/commit/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ approval_id: commitApproval.approval_id }),
      })
      setCommitApproval(null)
      setGitReviewMessage(`Local commit created: ${result.commit_sha.slice(0, 12)}. Nothing was pushed.`)
      await refreshGit()
      await loadGitReview()
      setBottomTab('review')
    } catch (error) {
      setCommitApproval(null)
      setGitReviewMessage(error instanceof Error ? error.message : 'Commit approval failed.')
      await loadGitReview().catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }

  async function disconnectWorkspace() {
    if (!workspaceId) return
    setBusy(true)
    try {
      await api<void>(`/api/v1/workspaces/${workspaceId}`, { method: 'DELETE' })
    } finally {
      setWorkspaceId('')
      setEntries([])
      setTreePath('')
      setSelectedPath('')
      setFileContent('')
      setSavedContent('')
      setFileSha('')
      setGitStatus('No workspace connected.')
      setGitDiff('No diff available.')
      setGitReview(null)
      setCommitApproval(null)
      setTerminalOutput('Workspace commands will appear here.')
      setWorkspaceMessage('Workspace destroyed. Connect another repository when ready.')
      setGitReviewMessage('Review changes before creating a local commit. Remote push is disabled.')
      setAgentMessage('Connect a repository to give Aquila a real workspace context.')
      setBusy(false)
    }
  }

  function mentionProvider(alias: string) {
    const mention = `@${alias}`
    setPrompt((current) => current.includes(mention) ? current : `${mention} ${current}`.trim())
  }

  function updateProjectPolicy(field: keyof ProjectPolicy, value: string | number) {
    setProjectPolicy((current) => {
      const next = { ...current, [field]: value } as ProjectPolicy
      if (repositoryUrl.trim() && typeof window !== 'undefined') {
        window.localStorage.setItem(projectPolicyKey(repositoryUrl), JSON.stringify(next))
      }
      return next
    })
  }

  async function submitAgent(event: FormEvent) {
    event.preventDefault()
    if (!prompt.trim()) return
    setBusy(true)
    setAgentMessage('Aquila is routing this request through the selected provider policy…')
    if (mode === 'build' || mode === 'debug') {
      setCommitApproval(null)
      setGitReviewMessage('Aquila may change the working tree. A fresh commit review will be required afterward.')
    }
    try {
      const data = await api<AgentRun>('/api/v1/agent/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode,
          prompt,
          project_name: projectName,
          workspace_id: workspaceId || null,
          project_policy: projectPolicy,
        }),
      })
      const noticeText = data.notices.length ? `\n\n${data.notices.join('\n')}` : ''
      setAgentMessage(`${data.message}${noticeText}`)
      setAgentRoute(`@${data.provider_alias} · ${data.model}`)
      if (typeof data.git_diff === 'string' && data.git_diff.trim()) {
        setGitDiff(data.git_diff)
        setBottomTab('diff')
      }
      if (data.checkpoint_id) {
        setWorkspaceMessage(`Aquila checkpoint ${data.checkpoint_id.slice(0, 8)} created before agent edits.`)
      }
      if (workspaceId) {
        await refreshGit()
        await loadGitReview()
      }
    } catch (error) {
      setAgentMessage(error instanceof Error ? error.message : 'Aquila API is unavailable.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="mark">IC</span><div><strong>Inzozi Code</strong><small>AI software engineering workspace</small></div></div>
        <div className="project-pill"><span className={`status-dot ${workspaceId ? '' : 'idle'}`} /> {projectName} {repositoryRef && <span className="branch">{repositoryRef}</span>}</div>
        <div className="agent-name">Aquila <span>●</span></div>
      </header>

      <section className="workspace">
        <aside className="explorer panel">
          <div className="panel-title"><span>EXPLORER</span><button onClick={() => workspaceId && loadTree(workspaceId, '')} disabled={!workspaceId}>⌂</button></div>
          {!workspaceId ? (
            <form className="connect-form" onSubmit={connectRepository}>
              <strong>Connect repository</strong>
              <p>Clone a GitHub repository into a guarded workspace.</p>
              <label>Repository HTTPS URL<input value={repositoryUrl} onChange={(e) => setRepositoryUrl(e.target.value)} placeholder="https://github.com/owner/repo" /></label>
              <label>Branch / ref <span>optional</span><input value={repositoryRef} onChange={(e) => setRepositoryRef(e.target.value)} placeholder="main" /></label>
              <button disabled={busy}>{busy ? 'Connecting…' : 'Create workspace'}</button>
              <small>{workspaceMessage}</small>
            </form>
          ) : (
            <>
              <div className="repo-title">{projectName.toUpperCase()}</div>
              <div className="pathbar">
                <button disabled={!treePath} onClick={() => loadTree(workspaceId, parentPath(treePath))}>←</button>
                <span>/{treePath}</span>
              </div>
              <nav className="file-list">
                {entries.map((entry) => (
                  <button key={`${treePath}/${entry.name}`} className={`file-row ${joinPath(treePath, entry.name) === selectedPath ? 'selected' : ''}`} onClick={() => openEntry(entry)}>
                    <span>{entry.type === 'directory' ? '▸' : '·'}</span><span>{entry.name}</span>
                  </button>
                ))}
              </nav>
              <div className="explorer-footer"><span>{workspaceId.slice(0, 8)}</span><button onClick={disconnectWorkspace} disabled={busy}>Disconnect</button></div>
            </>
          )}
        </aside>

        <section className="editor panel">
          <div className="tabs">
            <button className="active">{selectedPath || 'Welcome'} {dirty && <span className="dirty-dot">●</span>}</button>
            {selectedPath && <button className="save-tab" onClick={saveFile} disabled={!dirty || busy}>{busy ? 'Working…' : dirty ? 'Save' : 'Saved'}</button>}
          </div>
          {selectedPath ? (
            <Editor
              height="100%"
              path={selectedPath}
              language={languageFromPath(selectedPath)}
              theme="vs-dark"
              value={fileContent}
              onChange={(value) => setFileContent(value ?? '')}
              options={{ automaticLayout: true, fontSize: 13, minimap: { enabled: false }, wordWrap: 'off', scrollBeyondLastLine: false, padding: { top: 16 }, renderWhitespace: 'selection' }}
            />
          ) : (
            <div className="empty-editor">
              <span className="empty-mark">IC</span>
              <h2>{workspaceId ? 'Repository connected' : 'Build with Aquila'}</h2>
              <p>{workspaceId ? 'Select a file or ask Aquila to plan, design, review, build, or debug the repository.' : 'Connect a repository to begin the real code-review-edit-test loop.'}</p>
              <small>{workspaceMessage}</small>
            </div>
          )}
        </section>

        <aside className="aquila panel">
          <div className="aquila-heading"><div><span className="spark">✦</span><strong>Aquila</strong></div><small>{agentRoute}</small></div>
          <div className="modes">
            {(['ask','plan','design','build','debug','review','deploy'] as Mode[]).map((item) => <button key={item} onClick={() => setMode(item)} className={mode === item ? 'active' : ''}>{item}</button>)}
          </div>
          <div className="provider-strip" aria-label="AI providers and external agents">
            <span className="provider-label">REFERENCE</span>
            <div>
              {providers.map((provider) => (
                <button
                  key={provider.alias}
                  type="button"
                  onClick={() => mentionProvider(provider.alias)}
                  className={provider.configured ? 'provider-chip ready' : 'provider-chip pending'}
                  title={`${provider.display_name} · ${provider.configured ? 'ready' : 'registered, connector pending'}`}
                >
                  @{provider.alias}<i>{provider.configured ? '●' : '○'}</i>
                </button>
              ))}
            </div>
          </div>
          <details className="routing-policy">
            <summary>Project AI roles</summary>
            <div className="routing-policy-grid">
              <label>Primary<select value={projectPolicy.primary_provider} onChange={(e) => updateProjectPolicy('primary_provider', e.target.value)}>{providers.map((provider) => <option key={`primary-${provider.alias}`} value={provider.alias}>@{provider.alias}</option>)}</select></label>
              <label>Design<select value={projectPolicy.design_provider} onChange={(e) => updateProjectPolicy('design_provider', e.target.value)}>{providers.map((provider) => <option key={`design-${provider.alias}`} value={provider.alias}>@{provider.alias}</option>)}</select></label>
              <label>Reviewer<select value={projectPolicy.review_provider} onChange={(e) => updateProjectPolicy('review_provider', e.target.value)}>{providers.map((provider) => <option key={`review-${provider.alias}`} value={provider.alias}>@{provider.alias}</option>)}</select></label>
            </div>
            <small>Explicit @mentions override these defaults. Alpha preferences are stored locally per repository; no credentials are stored here.</small>
          </details>
          <div className="agent-output"><span className="output-label">AQUILA / {mode.toUpperCase()}</span><p>{agentMessage}</p><div className="context-card"><span>CONTEXT</span><strong>{workspaceId ? projectName : 'No workspace'}</strong><small>{selectedPath || 'No file selected'}</small></div></div>
          <form onSubmit={submitAgent} className="prompt-box">
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Aquila prompt" placeholder="Try: @chatgpt review this API, or @lovable propose a UI direction" />
            <div><span>{mode === 'deploy' ? 'Plan only · approval required' : mode === 'design' ? 'Read-only design review' : workspaceId ? 'Guarded workspace' : 'No repository tools'}</span><button disabled={busy}>{busy ? 'Running…' : 'Run →'}</button></div>
          </form>
        </aside>
      </section>

      <section className="bottom panel">
        <div className="bottom-tabs">
          <button onClick={() => setBottomTab('terminal')} className={bottomTab === 'terminal' ? 'active' : ''}>TERMINAL</button>
          <button onClick={() => { setBottomTab('status'); refreshGit() }} className={bottomTab === 'status' ? 'active' : ''}>GIT STATUS</button>
          <button onClick={() => { setBottomTab('diff'); refreshGit() }} className={bottomTab === 'diff' ? 'active' : ''}>GIT DIFF</button>
          <button onClick={() => { setBottomTab('review'); loadGitReview() }} className={bottomTab === 'review' ? 'active' : ''}>GIT REVIEW</button>
          <span className="command-spacer" />
          <button disabled={!workspaceId || busy} onClick={() => runAction('git_status')}>status</button>
          <button disabled={!workspaceId || busy} onClick={() => runAction('python_tests')}>pytest</button>
          <button disabled={!workspaceId || busy} onClick={() => runAction('node_build')}>node build</button>
          <button disabled={!workspaceId || busy} onClick={() => runAction('php_tests')}>php tests</button>
        </div>
        {bottomTab !== 'review' ? (
          <pre className="terminal-output">{bottomTab === 'terminal' ? terminalOutput : bottomTab === 'status' ? gitStatus : gitDiff}</pre>
        ) : (
          <div className="git-review-panel">
            <div className="git-review-summary">
              <div>
                <span className="git-review-eyebrow">HUMAN APPROVAL GATE</span>
                <strong>{gitReview?.branch || 'No workspace branch'}</strong>
                <small>{gitReview?.protected_branch ? 'Protected branch · create a safe branch before commit' : 'Local commit only · remote push disabled'}</small>
              </div>
              <button type="button" disabled={!workspaceId || busy} onClick={() => loadGitReview()}>Refresh review</button>
            </div>

            <div className="git-review-controls">
              {gitReview?.protected_branch ? (
                <form onSubmit={createSafeBranch} className="git-review-form">
                  <label>Safe branch<input value={branchName} onChange={(e) => setBranchName(e.target.value)} placeholder="feature/project-change" /></label>
                  <button disabled={busy || !workspaceId}>Create local branch</button>
                </form>
              ) : (
                <form onSubmit={prepareCommit} className="git-review-form">
                  <label>Commit message<input value={commitMessage} onChange={(e) => setCommitMessage(e.target.value)} maxLength={120} /></label>
                  <button disabled={busy || !workspaceId || !gitReview?.dirty}>Prepare commit</button>
                </form>
              )}

              {commitApproval && (
                <div className="commit-approval-card">
                  <div><span>REVIEW LOCKED</span><strong>{commitApproval.changed_paths.length} changed path{commitApproval.changed_paths.length === 1 ? '' : 's'}</strong></div>
                  <small>Expires {approvalTime(commitApproval.expires_at)} · tree {commitApproval.tree_hash.slice(0, 12)}</small>
                  <button type="button" disabled={busy} onClick={approveCommit}>Approve local commit</button>
                </div>
              )}
            </div>

            <div className="git-review-message">{gitReviewMessage}</div>
            <pre className="git-review-diff">{commitApproval?.diff || gitReview?.diff || 'No reviewed diff. Refresh Git Review after making changes.'}</pre>
          </div>
        )}
      </section>

      <footer><span>Workspace: {workspaceId ? `guarded · ${workspaceId.slice(0, 8)}` : 'disconnected'}</span><span>Route: {agentRoute}</span><span>Git: local commit gate · push disabled</span><span className="healthy">● safe policy</span></footer>
    </main>
  )
}
