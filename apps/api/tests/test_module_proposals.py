from app.product.engineering_sync import _proposal_catalog


def proposal_names(text: str, capabilities: set[str] | None = None) -> list[str]:
    return [name for name, _, _ in _proposal_catalog(text, capabilities or set())]


def test_notebook_workspace_is_not_misclassified_as_school_management() -> None:
    names = proposal_names(
        "Inzozi NoteBook is a knowledge workspace for professionals, students and academics. "
        "It includes CoreMind, timestamped transcript capture, source provenance, a workbench, "
        "mind maps and an academic document library."
    )

    assert names[0] == "Notebook & Knowledge Capture"
    assert "CoreMind & Provenance" in names
    assert "Library & Source Reader" in names
    assert "Admissions & Enrollment" not in names
    assert "Student Management" not in names


def test_school_management_requires_explicit_school_intent() -> None:
    names = proposal_names(
        "Build a School Management solution for primary and secondary schools with "
        "student admissions, school fees, attendance, grades and a parent/student portal."
    )

    assert names[0] == "Admissions & Enrollment"
    assert "Student Management" in names
    assert "Notebook & Knowledge Capture" not in names


def test_single_academic_or_student_reference_does_not_trigger_school_modules() -> None:
    names = proposal_names(
        "A professional research service for academic evidence review used by students and lawyers.",
        {"external_integrations"},
    )

    assert "Admissions & Enrollment" not in names
    assert "Student Management" not in names
    assert "Core Records & Profiles" in names
    assert "External Integrations" in names
