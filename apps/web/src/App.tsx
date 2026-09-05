import Editor from '@monaco-editor/react'
import { FormEvent, useMemo, useState } from 'react'

type Mode = 'ask' | 'plan' | 'build' | 'debug' | 'review' | 'deploy'
type BottomTab = 'terminal' | 'status' | 'diff'
type TreeEntry = { name: string; type: 'directory' | 'file' }
type FilePayload = { path: string; content: string; sha256: string }
type CommandResult = { exit_code: number; output: string; timed_out: boolean; action?: string }

type ApiError = { detail?: string }

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

export default function App() {
  const [mode, setMode] = useState<Mode>('plan')
  const [prompt, setPrompt] = useState('Review this project and propose the safest implementation plan.')
  const [agentMessage, setAgentMessage] = useState('Connect a repository to give Aquila a real workspace context.')

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
  const [busy, setBusy] = useState(false)
  const [workspaceMessage, setWorkspaceMessage] = useState('Public GitHub HTTPS repositories are supported in this milestone.')

  const dirty = fileContent !== savedContent
  const projectName = useMemo(() => repoLabel(repositoryUrl), [repositoryUrl])

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
      await loadTree(payload.workspace_id)
      await refreshGit(payload.workspace_id)
      setWorkspaceMessage('Workspace ready. Select a file to begin.')
      setAgentMessage('Aquila now has a connected workspace. Live model execution remains intentionally disabled.')
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
      setWorkspaceMessage(`Saved ${selectedPath}`)
      await refreshGit()
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
    try {
      const result = await api<CommandResult>(`/api/v1/workspaces/${workspaceId}/actions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      })
      setTerminalOutput(`${action}\n\n${result.output || '(no output)'}\n\nexit code: ${result.exit_code}${result.timed_out ? ' · timed out' : ''}`)
      await refreshGit()
    } catch (error) {
      setTerminalOutput(error instanceof Error ? error.message : 'Command failed.')
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
      setTerminalOutput('Workspace commands will appear here.')
      setWorkspaceMessage('Workspace destroyed. Connect another repository when ready.')
      setBusy(false)
    }
  }

  async function submitAgent(event: FormEvent) {
    event.preventDefault()
    if (!prompt.trim()) return
    setBusy(true)
    try {
      const data = await api<{ message?: string }>('/api/v1/agent/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, prompt, project_name: projectName }),
      })
      setAgentMessage(data.message ?? 'Aquila returned an empty response.')
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
              <p>{workspaceId ? 'Select a file from Explorer. Changes are written only through the guarded workspace API.' : 'Connect a repository to begin the real code-review-edit-test loop.'}</p>
              <small>{workspaceMessage}</small>
            </div>
          )}
        </section>

        <aside className="aquila panel">
          <div className="aquila-heading"><div><span className="spark">✦</span><strong>Aquila</strong></div><small>{workspaceId ? 'Workspace connected' : 'Waiting for repository'}</small></div>
          <div className="modes">
            {(['ask','plan','build','debug','review','deploy'] as Mode[]).map((item) => <button key={item} onClick={() => setMode(item)} className={mode === item ? 'active' : ''}>{item}</button>)}
          </div>
          <div className="agent-output"><span className="output-label">AQUILA / {mode.toUpperCase()}</span><p>{agentMessage}</p><div className="context-card"><span>CONTEXT</span><strong>{workspaceId ? projectName : 'No workspace'}</strong><small>{selectedPath || 'No file selected'}</small></div></div>
          <form onSubmit={submitAgent} className="prompt-box">
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Aquila prompt" />
            <div><span>{mode === 'deploy' ? 'Approval required' : workspaceId ? 'Guarded workspace' : 'Mock provider'}</span><button disabled={busy}>{busy ? 'Running…' : 'Run →'}</button></div>
          </form>
        </aside>
      </section>

      <section className="bottom panel">
        <div className="bottom-tabs">
          <button onClick={() => setBottomTab('terminal')} className={bottomTab === 'terminal' ? 'active' : ''}>TERMINAL</button>
          <button onClick={() => { setBottomTab('status'); refreshGit() }} className={bottomTab === 'status' ? 'active' : ''}>GIT STATUS</button>
          <button onClick={() => { setBottomTab('diff'); refreshGit() }} className={bottomTab === 'diff' ? 'active' : ''}>GIT DIFF</button>
          <span className="command-spacer" />
          <button disabled={!workspaceId || busy} onClick={() => runAction('git_status')}>status</button>
          <button disabled={!workspaceId || busy} onClick={() => runAction('python_tests')}>pytest</button>
          <button disabled={!workspaceId || busy} onClick={() => runAction('node_build')}>node build</button>
          <button disabled={!workspaceId || busy} onClick={() => runAction('php_tests')}>php tests</button>
        </div>
        <pre className="terminal-output">{bottomTab === 'terminal' ? terminalOutput : bottomTab === 'status' ? gitStatus : gitDiff}</pre>
      </section>

      <footer><span>Workspace: {workspaceId ? `guarded · ${workspaceId.slice(0, 8)}` : 'disconnected'}</span><span>Provider: mock-v0</span><span>Environment: staging bootstrap</span><span className="healthy">● {workspaceId ? 'workspace ready' : 'safe mode'}</span></footer>
    </main>
  )
}
