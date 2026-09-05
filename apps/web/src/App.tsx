import { FormEvent, useState } from 'react'

type Mode = 'ask' | 'plan' | 'build' | 'debug' | 'review' | 'deploy'

const files = [
  ['app', '▾'],
  ['routes', '▾'],
  ['resources', '▾'],
  ['database', '▾'],
  ['tests', '▾'],
  ['README.md', ''],
]

const codeSample = `class GrantApprovalService
{
    public function approve(Grant $grant, User $reviewer): Grant
    {
        // Aquila will edit project code here in the workspace milestone.
        $grant->status = 'approved';
        $grant->reviewed_by = $reviewer->id;
        $grant->save();

        return $grant;
    }
}`

export default function App() {
  const [mode, setMode] = useState<Mode>('plan')
  const [prompt, setPrompt] = useState('Review this project and propose the safest implementation plan.')
  const [message, setMessage] = useState('Aquila is ready. This bootstrap is running in mock-provider mode.')
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!prompt.trim()) return
    setBusy(true)
    try {
      const response = await fetch('/api/v1/agent/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, prompt, project_name: 'GrantFlow Demo' }),
      })
      const data = await response.json()
      setMessage(data.message ?? 'Aquila returned an empty response.')
    } catch {
      setMessage('The API is not reachable yet. Start the Docker stack or API service and try again.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="mark">IC</span><div><strong>Inzozi Code</strong><small>AI software engineering workspace</small></div></div>
        <div className="project-pill"><span className="status-dot" /> GrantFlow Demo <span className="branch">feature/v0-1</span></div>
        <div className="agent-name">Aquila <span>●</span></div>
      </header>

      <section className="workspace">
        <aside className="explorer panel">
          <div className="panel-title"><span>EXPLORER</span><button>＋</button></div>
          <div className="repo-title">INZOZI / GRANTFLOW</div>
          <nav>
            {files.map(([name, caret]) => <button key={name} className="file-row"><span>{caret}</span>{name}</button>)}
          </nav>
          <div className="explorer-footer"><span>Git</span><strong>8 changes</strong></div>
        </aside>

        <section className="editor panel">
          <div className="tabs"><button className="active">GrantApprovalService.php <span>×</span></button><button>Grant.php</button></div>
          <div className="editor-meta"><span>PHP</span><span>Ln 14, Col 9</span></div>
          <pre><code>{codeSample}</code></pre>
        </section>

        <aside className="aquila panel">
          <div className="aquila-heading"><div><span className="spark">✦</span><strong>Aquila</strong></div><small>Project-aware engineering agent</small></div>
          <div className="modes">
            {(['ask','plan','build','debug','review','deploy'] as Mode[]).map(item => (
              <button key={item} onClick={() => setMode(item)} className={mode === item ? 'active' : ''}>{item}</button>
            ))}
          </div>
          <div className="agent-output"><span className="output-label">AQUILA / {mode.toUpperCase()}</span><p>{message}</p></div>
          <form onSubmit={submit} className="prompt-box">
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label="Aquila prompt" />
            <div><span>{mode === 'deploy' ? 'Approval required' : 'Workspace safe mode'}</span><button disabled={busy}>{busy ? 'Running…' : 'Run →'}</button></div>
          </form>
        </aside>
      </section>

      <section className="bottom panel">
        <div className="bottom-tabs"><button className="active">TERMINAL</button><button>PROBLEMS <span>0</span></button><button>TESTS</button><button>GIT <span>8</span></button><button>PREVIEW</button><button>DEPLOYMENT</button></div>
        <div className="terminal"><span className="prompt">inzozi@workspace:~/grantflow$</span> php artisan test<br/><span className="muted">PASS  Tests\\Feature\\GrantApprovalTest · 14 tests · 42 assertions</span><br/><span className="prompt">inzozi@workspace:~/grantflow$</span> <span className="cursor">█</span></div>
      </section>

      <footer><span>Workspace: isolated</span><span>Provider: mock-v0</span><span>Environment: staging bootstrap</span><span className="healthy">● healthy</span></footer>
    </main>
  )
}
