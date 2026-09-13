from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_module_deliverable_planning_ui_contract():
    source = (ROOT / "apps/web/src/SolutionModules.tsx").read_text()
    styles = (ROOT / "apps/web/src/module-deliverable-planning.css").read_text()

    assert "+ New deliverable" in source
    assert "Create planned work directly under" in source
    assert "Create deliverable" in source
    assert "Attach existing deliverable" in source
    assert "/engineering/modules/${module.module_id}/deliverables" in source
    assert "No deliverables planned for this module yet." in source
    assert "studio-module-deliverable-form" in source
    assert ".studio-module-deliverable-form" in styles
    assert "@media (max-width: 900px)" in styles


def test_module_deliverable_planning_api_contract_source():
    route = (ROOT / "apps/api/app/routes/product_engineering.py").read_text()
    service = (ROOT / "apps/api/app/product/module_delivery.py").read_text()

    assert '@router.post("/modules/{module_id}/deliverables", status_code=201)' in route
    assert 'require_permission(request, "workspace:edit")' in route
    assert "create_module_deliverable" in route
    assert "INSERT INTO aquila_product_deliverables" in service
    assert "INSERT INTO aquila_product_module_deliverables" in service
    assert "FOR UPDATE" in service
    assert "'planned'" in service
