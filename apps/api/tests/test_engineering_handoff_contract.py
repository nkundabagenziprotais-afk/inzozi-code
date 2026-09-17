from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "web" / "src" / "InzoziSolutionSync.tsx"
BACKEND = ROOT / "api" / "app" / "product" / "engineering_sync.py"


def test_engineering_handoff_requires_exact_context_before_navigation():
    source = FRONTEND.read_text()

    assert "const contextSelectionTouchedRef = useRef(false)" in source
    assert "const handoffApprovedRef = useRef(false)" in source
    assert "const handoffInFlightRef = useRef(false)" in source

    assert "selectedDelivery.module_id !== selectedModuleId" in source
    assert "bound.binding.module_id !== selectedModuleId" in source
    assert "bound.binding.deliverable_id !== selectedDeliverableId" in source

    assert "'engineering_opened'" not in source


def test_stale_summary_cannot_overwrite_explicit_selector_context():
    source = FRONTEND.read_text()

    assert "if (!contextSelectionTouchedRef.current)" in source
    assert "contextSelectionTouchedRef.current = true" in source


def test_navigation_event_is_not_a_qualifying_progress_event():
    source = BACKEND.read_text()

    start = source.index("ENGINEERING_PROGRESS_EVENT_TYPES")
    end = source.index("ENGINEERING_EVENT_STATUSES", start)
    progress_types = source[start:end]

    assert '"engineering_opened"' not in progress_types
    assert '"workspace_opened"' in progress_types
    assert '"command_completed"' in progress_types
    assert '"git_review_prepared"' in progress_types
    assert '"commit_created"' in progress_types
    assert '"push_completed"' in progress_types
    assert '"pull_request_created"' in progress_types
    assert "event_type in ENGINEERING_PROGRESS_EVENT_TYPES" in source
