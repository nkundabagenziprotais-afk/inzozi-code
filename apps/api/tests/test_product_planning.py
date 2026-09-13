from app.routes.products import ModuleSeedRequest, ProductCreateRequest, build_initial_product_plan


def test_guided_discovery_builds_platform_backend_and_solution_modules():
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
        modules=[
            ModuleSeedRequest(
                name="Patient Registration",
                description="Register and maintain patient profiles.",
                priority="must_have",
            ),
            ModuleSeedRequest(
                name="Clinical Consultation",
                description="Document consultations and care plans.",
                priority="must_have",
            ),
            ModuleSeedRequest(
                name="Management Dashboard",
                description="Provide executive operational views.",
                priority="should_have",
            ),
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

    assert [item["name"] for item in plan["modules"]] == [
        "Patient Registration",
        "Clinical Consultation",
        "Management Dashboard",
    ]
    assert [item["priority"] for item in plan["modules"]] == [
        "must_have",
        "must_have",
        "should_have",
    ]
    assert all(item["status"] == "planned" for item in plan["modules"])


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

    assert plan["modules"] == []
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


def test_duplicate_seed_modules_are_collapsed_case_insensitively():
    payload = ProductCreateRequest(
        name="Retail Platform",
        concept="Create a retail platform with a product catalogue and controlled checkout experience.",
        platforms=["web"],
        modules=[
            ModuleSeedRequest(name="Product Catalogue", priority="must_have"),
            ModuleSeedRequest(name=" product catalogue ", priority="good_to_have"),
            ModuleSeedRequest(name="Checkout", priority="must_have"),
        ],
    )

    modules = build_initial_product_plan(payload)["modules"]

    assert len(modules) == 2
    assert modules[0]["name"] == "Product Catalogue"
    assert modules[0]["priority"] == "must_have"
    assert modules[1]["name"] == "Checkout"
