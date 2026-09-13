import React from 'react'
import ReactDOM from 'react-dom/client'
import AuthGate from './AuthGate'
import InzoziSolutionSync from './InzoziSolutionSync'
import StudioShell from './StudioShell'
import './styles.css'
import './project-policy.css'
import './git-review.css'
import './pull-request.css'
import './repository-history.css'
import './auth.css'
import './studio-shell.css'
import './studio-shell-refinement.css'
import './studio-live-review.css'
import './studio-engineering-sync.css'
import './module-delivery.css'
import './unified-shell.css'
import './brand.css'
import './inzozi-engineering-experience.css'
import './unified-shell'
import './inzozi-engineering-experience'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthGate>
      <InzoziSolutionSync>
        <StudioShell />
      </InzoziSolutionSync>
    </AuthGate>
  </React.StrictMode>,
)
