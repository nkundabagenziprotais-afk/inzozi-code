# Aquila Studio v0.2 — Product-Control Foundation

## Product direction

Aquila Studio is evolving from a repository-first AI engineering workspace into an end-to-end software delivery operating system for both non-technical and technical users.

The user-facing lifecycle is:

`Idea -> Plan -> Architecture -> Build -> Test -> Certify -> Deploy -> Operate -> Improve -> Reuse`

The existing Inzozi Code engineering runtime remains a protected subsystem. Secure workspaces, repository inspection, Aquila build/debug/review modes, tests, Git approvals, identity, monitoring, backup and rollback are retained rather than replaced.

## Architectural correction

The primary domain object is now the **Product**, not the repository.

A Product contains:

- a durable Product Blueprint;
- target users and platform targets;
- system components;
- ordered deliverables;
- dependency relationships;
- later releases, environments, certification, documentation and reusable assets;
- one or more engineering resources such as repositories and guarded workspaces.

The repository remains the implementation source of truth beneath the Product.

## v0.2 scope

This first product-control slice introduces:

1. persistent PostgreSQL Product records scoped by organization;
2. durable Product Blueprints;
3. system Components;
4. ordered Deliverables;
5. Component Dependencies;
6. Guided Discovery from plain-language concept + checkbox platform/capability selection;
7. automatic first-pass delivery sequence and dependency mapping;
8. Project Control Center showing progress, scope, deliverables, components and dependencies;
9. a direct handoff from Product Control to the existing Engineering Workspace;
10. preservation of the existing engineering runtime and its approval boundaries.

## Product-control data model

### `aquila_products`

The durable solution/project identity: organization, owner, name, summary and lifecycle status.

### `aquila_product_blueprints`

The problem statement and planning inputs: concept, target users, platform targets, supporting capabilities, constraints and blueprint version.

### `aquila_product_components`

Independently managed parts of the product, including:

- solution architecture;
- security/governance;
- web/mobile/desktop/wearable experiences;
- APIs;
- data platform;
- integrations;
- reporting;
- notifications;
- offline synchronization;
- documentation;
- operations/reliability.

### `aquila_product_deliverables`

The ordered sequence of work visible in the Project Control Center. Each deliverable is `planned`, `in_progress`, `blocked` or `complete`.

### `aquila_product_dependencies`

Explicit relationships between components. This is the initial foundation for the later change-impact engine.

## Guided user journey

The default Aquila Studio path becomes:

`Login -> Products -> Create Product -> Guided Discovery -> Product Blueprint -> Project Control Center -> Engineering Workspace`

A non-technical user is not required to provide a repository during product discovery.

The Guided Discovery interface asks for:

- the product/solution name;
- the business concept in ordinary language;
- target users;
- target experiences: web, Windows, macOS, Android/iOS phone, Android/iPad tablet and wearable;
- supporting capabilities: database, internal APIs, external integrations, reporting, notifications, offline behavior, documentation and monitoring;
- known constraints.

The system converts those selections into the first durable plan.

## Existing engineering runtime

The current repository/workspace/editor/Aquila/Git experience becomes **Engineering Workspace** and remains available from Product Control as an advanced execution surface.

The v0.2 slice intentionally does not remove or weaken:

- workspace isolation;
- workspace ownership controls;
- restricted egress;
- checkpoints;
- test/build commands;
- Git diff/review;
- commit approval;
- push approval;
- draft pull-request approval;
- protected-main governance;
- staging monitoring/backup/rollback controls.

## Explicit non-goals for this slice

This PR does **not** implement or authorize:

- public DNS activation;
- public-edge/firewall widening;
- production deployment;
- automatic merge;
- direct production mutation;
- full change-impact execution;
- reusable module cloning;
- environment provisioning;
- production release orchestration;
- certification workflows;
- automatic user-manual generation.

Those capabilities build on the Product model introduced here.

## Next product slices

1. **Product/Component change center** — change requests and impact analysis against the dependency graph.
2. **Engineering binding** — attach repositories/workspaces to specific Product Components rather than opening repositories globally.
3. **Environment model** — Development, Test, UAT, Staging and Production as Product resources.
4. **Quality and certification** — evidence-backed component/release certification.
5. **Reusable asset library** — publish, version, borrow and customize modules/features/workflows.
6. **Documentation center** — living manuals, API documentation, release notes and training material.
7. **Release manager** — dependency-aware deployment planning and rollback evidence.

## Acceptance intent

The v0.2 Product-Control Foundation is acceptable when:

- a signed-in editable user can create a Product without selecting a repository;
- Product state persists in PostgreSQL and is organization-scoped;
- the first delivery plan is generated from selected platform/capability scope;
- Project Control shows progress, deliverables, components and dependency relationships;
- deliverable status can be updated by users who already hold engineering edit permission;
- read-only roles cannot mutate product planning state;
- the existing Engineering Workspace remains reachable and unchanged in capability;
- existing CI remains green;
- no staging, DNS, firewall, production or Terraform mutation occurs as part of the implementation PR.
