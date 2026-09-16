(() => {
  const menuToggle = document.querySelector('[data-menu-toggle]')
  const nav = document.querySelector('[data-nav]')
  const header = document.querySelector('[data-header]')
  const year = document.querySelector('[data-year]')

  if (year) year.textContent = String(new Date().getFullYear())

  function closeMenu() {
    if (!menuToggle || !nav) return
    menuToggle.setAttribute('aria-expanded', 'false')
    nav.classList.remove('open')
  }

  menuToggle?.addEventListener('click', () => {
    if (!nav) return
    const nextOpen = menuToggle.getAttribute('aria-expanded') !== 'true'
    menuToggle.setAttribute('aria-expanded', String(nextOpen))
    nav.classList.toggle('open', nextOpen)
  })

  nav?.querySelectorAll('a').forEach((link) => {
    link.addEventListener('click', closeMenu)
  })

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenu()
  })

  window.addEventListener('resize', () => {
    if (window.innerWidth > 860) closeMenu()
  })

  window.addEventListener('scroll', () => {
    header?.classList.toggle('scrolled', window.scrollY > 12)
  }, { passive: true })
})()
