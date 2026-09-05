# Inzozi Code

**Inzozi Code** is an AI-native software engineering and deployment platform for Inzozi Digital. The product is designed to run at `code.inzozidigital.com`, with staging at `code-staging.inzozidigital.com`.

The AI engineering agent inside the product is **Aquila**.

## V0.1 goal

Prove the safe development loop before production deployment:

1. Open/connect a repository.
2. Inspect project files.
3. Plan a change.
4. Edit multiple files.
5. Run commands in an isolated workspace.
6. Run tests/builds.
7. Preview the application.
8. Show Git diff for review.

This bootstrap intentionally starts with a **mock Aquila provider**. No production AI API key is required to launch the shell. The provider interface will be connected after the repository and staging environment are established.

## Architecture

- Web: React + TypeScript + Vite
- API: FastAPI + Python 3.12
- Data: PostgreSQL
- Queue/cache: Redis
- Proxy: Nginx
- Runtime: Docker Compose for the bootstrap
- Source of truth: GitHub
- Deployment targets: cPanel / VPS / cloud connectors (later milestones)

## Local start

```bash
cp .env.example .env
docker compose up --build
```

Then open:

- Web: http://localhost:8080
- API health: http://localhost:8000/health

## Repository policy

- Never push product work directly to `main`.
- Use branches such as `feature/inzozi-code-v0-1`.
- cPanel is a deployment target, not the source of truth.
- Production actions require an approval policy and audit trail.
- AI-generated commands must execute inside isolated workspaces, never directly on the host.

See `docs/architecture.md` and `docs/v0.1-acceptance.md`.
