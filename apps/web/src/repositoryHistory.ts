export type RepositoryHistoryItem = {
  repository_url: string
  ref: string
  last_opened_at: string
}

const REPOSITORY_HISTORY_KEY = 'inzozi-code:repository-history:v1'
const MAX_REPOSITORY_HISTORY = 8

function normalizeRepositoryUrl(repositoryUrl: string) {
  return repositoryUrl.trim().replace(/\.git$/, '')
}

function normalizeItem(item: RepositoryHistoryItem): RepositoryHistoryItem | null {
  const repositoryUrl = normalizeRepositoryUrl(item.repository_url)
  if (!/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(repositoryUrl)) return null
  return {
    repository_url: repositoryUrl,
    ref: typeof item.ref === 'string' ? item.ref.trim().slice(0, 160) : '',
    last_opened_at: typeof item.last_opened_at === 'string' ? item.last_opened_at : new Date(0).toISOString(),
  }
}

export function loadRepositoryHistory(): RepositoryHistoryItem[] {
  if (typeof window === 'undefined') return []
  try {
    const parsed = JSON.parse(window.localStorage.getItem(REPOSITORY_HISTORY_KEY) || '[]')
    if (!Array.isArray(parsed)) return []
    return parsed
      .map((item) => normalizeItem(item as RepositoryHistoryItem))
      .filter((item): item is RepositoryHistoryItem => Boolean(item))
      .slice(0, MAX_REPOSITORY_HISTORY)
  } catch {
    return []
  }
}

function persistRepositoryHistory(items: RepositoryHistoryItem[]) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(REPOSITORY_HISTORY_KEY, JSON.stringify(items.slice(0, MAX_REPOSITORY_HISTORY)))
}

export function rememberRepository(repositoryUrl: string, ref: string): RepositoryHistoryItem[] {
  const normalizedUrl = normalizeRepositoryUrl(repositoryUrl)
  const normalizedRef = ref.trim().slice(0, 160)
  const current = loadRepositoryHistory().filter(
    (item) => !(item.repository_url.toLowerCase() === normalizedUrl.toLowerCase() && item.ref === normalizedRef),
  )
  const next = [
    { repository_url: normalizedUrl, ref: normalizedRef, last_opened_at: new Date().toISOString() },
    ...current,
  ].slice(0, MAX_REPOSITORY_HISTORY)
  persistRepositoryHistory(next)
  return next
}

export function removeRememberedRepository(repositoryUrl: string, ref: string): RepositoryHistoryItem[] {
  const normalizedUrl = normalizeRepositoryUrl(repositoryUrl)
  const normalizedRef = ref.trim()
  const next = loadRepositoryHistory().filter(
    (item) => !(item.repository_url.toLowerCase() === normalizedUrl.toLowerCase() && item.ref === normalizedRef),
  )
  persistRepositoryHistory(next)
  return next
}
