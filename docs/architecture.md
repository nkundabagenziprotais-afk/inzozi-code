# Inzozi Code — V0.1 System Architecture

## Product identity

- Product: **Inzozi Code**
- Agent: **Aquila**
- Production: `code.inzozidigital.com`
- Staging: `code-staging.inzozidigital.com`

## Core principle

GitHub is the source of truth. cPanel/VPS/cloud systems are deployment targets. AI-generated code executes in isolated workspaces, not on the Inzozi Code host and not directly in production.

## Components

1. **Web IDE** — repository explorer, editor surface, Aquila panel, terminal, Git, preview and deployment areas.
2. **API** — authentication, projects, agent runs, approvals, audit log and deployment contracts.
3. **Aquila Orchestrator** — provider-agnostic reasoning layer. V0.1 bootstrap uses a mock provider.
4. **Workspace Manager** — creates isolated build/test environments and exposes narrow tools to the agent.
5. **Indexer** — later milestone for symbols, AST, repository search and embeddings.
6. **GitHub Connector** — branch, diff, commit, push and PR workflow.
7. **Deployment Service** — later milestone for cPanel, SSH VPS and cloud connectors.
8. **PostgreSQL** — users, projects, agent runs, memories, approvals, deployment history and audit log.
9. **Redis** — queues, locks, transient run state.

## Approval levels

### Safe
Read/search/analyze/plan/status/diff.

### Development
Create/edit files, install dependencies, run tests, builds and local migrations inside isolated workspaces.

### Production
Deployment actions run only through a deployment connector. Destructive/high-risk operations require explicit human approval and every action must be auditable.

## V0.1 scope

The bootstrap proves the product shell and API contract. The next engineering slice is repository connection + workspace manager + real Git diff. AI-provider integration follows the provider gateway rather than being hard-coded into the UI.
