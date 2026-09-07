import { KeyboardEvent, useEffect, useRef } from 'react'

type RemoveRepositoryDialogProps = {
  repositoryName: string
  repositoryRef: string
  busy: boolean
  onCancel: () => void
  onConfirm: () => void
}

export default function RemoveRepositoryDialog({
  repositoryName,
  repositoryRef,
  busy,
  onCancel,
  onConfirm,
}: RemoveRepositoryDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const previousFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    cancelRef.current?.focus()

    return () => {
      previousFocusRef.current?.focus()
    }
  }, [])

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') {
      event.preventDefault()
      if (!busy) onCancel()
      return
    }

    if (event.key !== 'Tab' || !dialogRef.current) return

    const focusable = Array.from(
      dialogRef.current.querySelectorAll<HTMLElement>('button:not(:disabled), [href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'),
    )
    if (!focusable.length) return

    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    const active = document.activeElement

    if (event.shiftKey && active === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && active === last) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <div className="repository-remove-backdrop" role="presentation">
      <div
        ref={dialogRef}
        className="repository-remove-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="repository-remove-title"
        aria-describedby="repository-remove-description"
        onKeyDown={handleKeyDown}
      >
        <button
          type="button"
          className="repository-remove-close"
          onClick={onCancel}
          disabled={busy}
          aria-label="Close remove confirmation"
        >
          ×
        </button>

        <div className="repository-remove-icon" aria-hidden="true">!</div>
        <span className="repository-remove-eyebrow">RECENT REPOSITORY</span>
        <h2 id="repository-remove-title">Remove from recent repositories?</h2>
        <p id="repository-remove-description">
          This removes the saved shortcut and local project preferences from this browser.
        </p>

        <div className="repository-remove-identity">
          <span>Repository</span>
          <strong>{repositoryName}</strong>
          <small>{repositoryRef || 'Default branch'}</small>
        </div>

        <div className="repository-remove-safety">
          <span className="repository-remove-safety-mark" aria-hidden="true">✓</span>
          <div>
            <strong>GitHub remains untouched</strong>
            <small>This action does not delete, modify, disconnect, or change the repository on GitHub.</small>
          </div>
        </div>

        <div className="repository-remove-actions">
          <button ref={cancelRef} type="button" className="repository-remove-cancel" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="repository-remove-confirm" onClick={onConfirm} disabled={busy}>
            {busy ? 'Removing…' : 'Remove'}
          </button>
        </div>
      </div>
    </div>
  )
}
