# Inzozi Digital Parent Website

This static application is the public parent-platform experience for `www.inzozidigital.com`.

## Product role

The website presents the Inzozi Digital ecosystem and routes visitors to active solution environments without pretending that planned products are already available.

Current hierarchy:

- Inzozi Digital — parent platform
  - Inzozi AI Solution — active at `https://ai.inzozidigital.com`
    - Inzozi AI Coding — active
    - Inzozi AI Professional — planned
    - Inzozi AI Research — planned
    - Inzozi AI Academia — planned
    - Inzozi AI Automation — planned
    - Inzozi AI Analytics — planned
    - Inzozi AI Creative — planned
  - Inzozi Financial Solution — planned
  - Inzozi Health Solution — planned
  - Inzozi Transport Solution — planned
  - Inzozi Education Solution — planned
  - Inzozi Agri Solution — planned
  - Inzozi Business Management Solution — planned

## Runtime

The site is intentionally static and independent from the Inzozi AI API, PostgreSQL, Redis and guarded workspace runtime.

The container exposes:

- `/` — website
- `/health` — liveness response
- `/ready` — readiness response

Public DNS/TLS/Nginx activation is a separate deployment gate after merge and exact-head CI.
