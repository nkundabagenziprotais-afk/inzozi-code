from app.routes.products import ProductCreateRequest, build_initial_product_plan


def test_guided_discovery_builds_platform_and_backend_components():
    payload = ProductCreateRequest(
        name="Hospital Management",
        concept=(
            "Create a hospital management solution for registration, clinical workflows, "
            "billing and patient follow-up across web and mobile experiences."
        ),
        target_users=["Administrator", "Doctor", "Nurse", "Cashier"],
        platforms=["web", "android_phone", "ios_phone"],
        capabilities=[
            "database",
            "internal_api",
            "external_integrations",
            "reporting",
            "documentation",
            "monitoring",
        ],
    )

    plan = build_initial_product_plan(payload)
    components = {item["component_key"]: item for item in plan["components"]}

    assert "solution-architecture" in components
    assert "security-governance" in components
    assert "data-platform" in components
    assert "application-api" in components
    assert "external-integrations" in components
    assert "reporting-analytics" in components
    assert "experience-web" in components
    assert "experience-android_phone" in components
    assert "experience-ios_phone" in components
    assert "documentation" in components
    assert "operations" in components

    assert components["experience-web"]["platform"] == "web"
    assert components["experience-android_phone"]["kind"] == "experience"


def test_initial_plan_is_sequenced_and_dependency_aware():
    payload = ProductCreateRequest(
        name="Field Service",
        concept=(
            "Create a field-service product where teams record visits on Android phones "
            "and synchronize operational data through a shared API."
        ),
        target_users=["Field Officer", "Supervisor"],
        platforms=["android_phone"],
        capabilities=["database", "internal_api", "offline", "documentation"],
    )

    plan = build_initial_product_plan(payload)
    deliverables = plan["deliverables"]
    dependencies = plan["dependencies"]

    assert [item["sequence"] for item in deliverables] == list(range(1, len(deliverables) + 1))
    assert deliverables[0]["title"] == "Product blueprint and discovery"
    assert deliverables[0]["status"] == "complete"
    assert deliverables[1]["status"] == "in_progress"
    assert deliverables[-1]["title"] == "Environment and release readiness"

    relationships = {
        (item["upstream_key"], item["downstream_key"], item["relationship"])
        for item in dependencies
    }
    assert ("data-platform", "application-api", "serves") in relationships
    assert ("application-api", "experience-android_phone", "supports") in relationships
    assert ("solution-architecture", "offline-sync", "defines") in relationships
