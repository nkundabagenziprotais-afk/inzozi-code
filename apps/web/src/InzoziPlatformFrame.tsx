import { ReactNode, useEffect, useState } from 'react'

const TOP_LEVEL_SOLUTIONS = [
  'Inzozi AI Solution',
  'Inzozi Financial Solution',
  'Inzozi Health Solution',
  'Inzozi Transport Solution',
  'Inzozi Education Solution',
  'Inzozi Agri Solution',
  'Inzozi Business Management Solution',
]

const AI_PRODUCTS = [
  'Inzozi AI Coding',
  'Inzozi AI Professional',
  'Inzozi AI Research',
  'Inzozi AI Academia',
  'Inzozi AI Automation',
  'Inzozi AI Analytics',
  'Inzozi AI Creative',
]

function reviewTab() {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('.bottom-tabs > button'))
    .find((button) => {
      const label = button.textContent?.trim().toUpperCase() ?? ''
      return label === 'GIT REVIEW' || label === 'DELIVERY REVIEW'
    })
}

function quickTab(label: string) {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('.bottom-tabs > button'))
    .find((button) => button.textContent?.trim().toUpperCase() === label)
}

export default function InzoziPlatformFrame({ children }: { children: ReactNode }) {
  const [deliveryReviewOpen, setDeliveryReviewOpen] = useState(false)

  useEffect(() => {
    function synchronizeReviewState() {
      setDeliveryReviewOpen(Boolean(reviewTab()?.classList.contains('active')))
    }

    synchronizeReviewState()
    const observer = new MutationObserver(synchronizeReviewState)
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['class'],
    })

    return () => observer.disconnect()
  }, [])

  function backToEngineering() {
    quickTab('TERMINAL')?.click()
    window.setTimeout(() => {
      const toggle = document.querySelector<HTMLButtonElement>('.bottom-toggle')
      if (toggle?.textContent?.includes('Collapse')) toggle.click()
    }, 0)
  }

  return (
    <div className={`inzozi-platform-frame ${deliveryReviewOpen ? 'delivery-review-open' : ''}`}>
      <header className="inzozi-platform-ribbon" aria-label="Inzozi Digital product hierarchy">
        <div className="inzozi-platform-breadcrumb">
          <div className="inzozi-platform-parent">
            <strong>Inzozi Digital</strong>
            <small>www.inzozidigital.com</small>
          </div>
          <span className="inzozi-platform-separator">›</span>
          <div>
            <strong>Inzozi AI Solution</strong>
            <small>ai.inzozidigital.com</small>
          </div>
          <span className="inzozi-platform-separator">›</span>
          <div className="inzozi-platform-current">
            <strong>Inzozi AI Coding</strong>
            <small>Current product</small>
          </div>
        </div>

        <div className="inzozi-platform-actions">
          {deliveryReviewOpen && (
            <button type="button" className="inzozi-back-engineering" onClick={backToEngineering}>
              ← Back to Engineering Space
            </button>
          )}

          <details className="inzozi-platform-menu">
            <summary>AI products</summary>
            <div className="inzozi-platform-menu-card">
              <span className="inzozi-platform-menu-kicker">INZOZI AI SOLUTION</span>
              {AI_PRODUCTS.map((product) => (
                <div className={product === 'Inzozi AI Coding' ? 'active' : ''} key={product}>
                  <strong>{product}</strong>
                  <small>{product === 'Inzozi AI Coding' ? 'Active on this workspace' : 'Planned product'}</small>
                </div>
              ))}
            </div>
          </details>

          <details className="inzozi-platform-menu">
            <summary>Solutions</summary>
            <div className="inzozi-platform-menu-card solutions">
              <span className="inzozi-platform-menu-kicker">INZOZI DIGITAL PLATFORM</span>
              {TOP_LEVEL_SOLUTIONS.map((solution) => (
                <div className={solution === 'Inzozi AI Solution' ? 'active' : ''} key={solution}>
                  <strong>{solution}</strong>
                  <small>{solution === 'Inzozi AI Solution' ? 'ai.inzozidigital.com' : 'Dedicated subdomain when activated'}</small>
                </div>
              ))}
              <div>
                <strong>Future solutions</strong>
                <small>The platform can expand without changing this hierarchy.</small>
              </div>
            </div>
          </details>
        </div>
      </header>

      <div className="inzozi-platform-product-shell">{children}</div>
    </div>
  )
}
