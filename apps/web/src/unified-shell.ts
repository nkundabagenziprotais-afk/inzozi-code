const UNIFIED_NAV_ATTRIBUTE = 'data-unified-nav'
const UNIFIED_HOME_ATTRIBUTE = 'data-unified-home'

function buttonByText(fragment: string): HTMLButtonElement | null {
  return Array.from(document.querySelectorAll<HTMLButtonElement>('button'))
    .find((button) => button.textContent?.includes(fragment)) ?? null
}

function goToProjectControl() {
  const returnButton = document.querySelector<HTMLButtonElement>('.studio-return-control')
  if (returnButton) {
    returnButton.click()
  }
}

function goToEngineering() {
  if (document.querySelector('.studio-engineering-mode')) return

  const engineeringButton = buttonByText('Open Engineering Workspace')
    ?? buttonByText('Engineering Workspace')

  engineeringButton?.click()
}

function goHome() {
  if (document.querySelector('.studio-engineering-mode')) {
    goToProjectControl()
    window.setTimeout(() => {
      document.querySelector<HTMLButtonElement>('.studio-brand')?.click()
    }, 60)
    return
  }

  document.querySelector<HTMLButtonElement>('.studio-brand')?.click()
}

function normalizeLegacyLabels() {
  document.querySelectorAll<HTMLElement>('.empty-editor .eyebrow').forEach((element) => {
    if (element.textContent?.trim() === 'INZOZI CODE ALPHA') {
      element.textContent = 'ENGINEERING SPACE'
    }
  })

  document.querySelectorAll<HTMLElement>('.auth-brand strong').forEach((element) => {
    if (element.textContent?.trim() === 'Inzozi Code') {
      element.textContent = 'Inzozi AI-Coding'
    }
  })

  document.querySelectorAll<HTMLElement>('.auth-loading strong').forEach((element) => {
    if (element.textContent?.includes('Opening Inzozi Code')) {
      element.textContent = 'Opening Inzozi AI-Coding…'
    }
  })

  const sessionChip = document.querySelector<HTMLElement>('.auth-session-chip')
  sessionChip?.setAttribute('aria-label', 'Signed-in Inzozi AI-Coding session')
}

function normalizeProjectControlHome() {
  const welcomeCopy = document.querySelector<HTMLElement>('.studio-welcome-copy p')
  if (welcomeCopy?.textContent?.includes('Aquila Studio turns an idea')) {
    welcomeCopy.textContent = 'Inzozi AI-Coding turns an idea into a product blueprint, system map, ordered deliverables and a synchronized engineering journey. Technical details remain available inside Engineering Space when you need them.'
  }

  const foundationHeading = document.querySelector<HTMLElement>('.studio-foundation-note strong')
  if (foundationHeading?.textContent?.trim() === 'Your existing Engineering Runtime is preserved.') {
    foundationHeading.textContent = 'Engineering Space is integrated into delivery.'
  }

  const foundationCopy = document.querySelector<HTMLElement>('.studio-foundation-note p')
  if (foundationCopy?.textContent?.includes('Secure workspaces, Aquila modes')) {
    foundationCopy.textContent = 'Plan and govern work here, then continue the selected module and deliverable in Engineering Space. Engineering evidence synchronizes back into Project Control.'
  }

  const syncBar = document.querySelector<HTMLElement>('.inzozi-sync-bar')
  const emptyProduct = document.querySelector<HTMLElement>('.inzozi-sync-product.empty')

  if (emptyProduct) {
    const emptySolution = emptyProduct.querySelector<HTMLElement>('strong')
    if (emptySolution && emptySolution.textContent?.trim() !== 'Select a solution') {
      emptySolution.textContent = 'Select a solution'
    }
    syncBar?.setAttribute('data-no-active-solution', 'true')
  } else {
    syncBar?.removeAttribute('data-no-active-solution')
  }
}

function enhanceSharedNavigation() {
  const brand = document.querySelector<HTMLElement>('.inzozi-sync-brand')
  if (brand && brand.getAttribute(UNIFIED_HOME_ATTRIBUTE) !== 'true') {
    brand.setAttribute(UNIFIED_HOME_ATTRIBUTE, 'true')
    brand.setAttribute('role', 'button')
    brand.tabIndex = 0
    brand.setAttribute('aria-label', 'Open Inzozi AI-Coding Project Control home')
  }

  document.querySelectorAll<HTMLElement>('.inzozi-sync-flow span').forEach((element) => {
    if (element.getAttribute(UNIFIED_NAV_ATTRIBUTE) === 'true') return
    const label = element.textContent?.trim()
    if (label !== 'Project Control Center' && label !== 'Engineering Space') return

    element.setAttribute(UNIFIED_NAV_ATTRIBUTE, 'true')
    element.setAttribute('role', 'button')
    element.tabIndex = 0
    element.setAttribute('aria-label', `Open ${label}`)
  })
}

function enhanceShell() {
  enhanceSharedNavigation()
  normalizeLegacyLabels()
  normalizeProjectControlHome()
}

function activateTarget(target: EventTarget | null) {
  if (!(target instanceof Element)) return false

  const nav = target.closest<HTMLElement>(`.inzozi-sync-flow span[${UNIFIED_NAV_ATTRIBUTE}='true']`)
  if (nav) {
    const label = nav.textContent?.trim()
    if (label === 'Project Control Center') goToProjectControl()
    if (label === 'Engineering Space') goToEngineering()
    return true
  }

  const brand = target.closest<HTMLElement>(`.inzozi-sync-brand[${UNIFIED_HOME_ATTRIBUTE}='true']`)
  if (brand) {
    goHome()
    return true
  }

  return false
}

if (typeof window !== 'undefined') {
  document.addEventListener('click', (event) => {
    activateTarget(event.target)
  })

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    if (!activateTarget(event.target)) return
    event.preventDefault()
  })

  const observer = new MutationObserver(() => enhanceShell())
  observer.observe(document.documentElement, { childList: true, subtree: true })

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', enhanceShell, { once: true })
  } else {
    enhanceShell()
  }
}
