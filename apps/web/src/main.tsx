import React from 'react'
import ReactDOM from 'react-dom/client'
import AuthGate from './AuthGate'
import StudioShell from './StudioShell'
import './styles.css'
import './project-policy.css'
import './git-review.css'
import './pull-request.css'
import './repository-history.css'
import './auth.css'
import './studio-shell.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthGate>
      <StudioShell />
    </AuthGate>
  </React.StrictMode>,
)
