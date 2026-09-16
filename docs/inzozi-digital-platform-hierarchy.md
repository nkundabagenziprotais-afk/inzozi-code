# Inzozi Digital platform hierarchy

## Purpose

This document defines the product and domain hierarchy for the Inzozi Digital ecosystem so individual applications can evolve without being mistaken for the parent platform or for an entire solution family.

## Level 1 — Inzozi Digital platform

**Primary platform:** `www.inzozidigital.com`

Inzozi Digital is the parent platform and future solution hub. It is intended to present and route users to the solution families below.

### Solution families

- Inzozi AI Solution
- Inzozi Financial Solution
- Inzozi Health Solution
- Inzozi Transport Solution
- Inzozi Education Solution
- Inzozi Agri Solution
- Inzozi Business Management Solution
- Additional solution families may be added later without changing the hierarchy.

Each non-AI solution family may use its own dedicated subdomain when activated. Exact future subdomain names are an infrastructure decision and are not defined by this document.

## Level 2 — Inzozi AI Solution

**Shared AI host:** `ai.inzozidigital.com`

All Inzozi AI products are grouped under the Inzozi AI Solution family and share the AI host unless a future architecture decision explicitly changes that model.

### Inzozi AI products

1. **Inzozi AI Coding** — current product under development; repository-aware AI coding, product control, engineering workspace, guarded delivery and Git approval workflows.
2. **Inzozi AI Professional** — planned.
3. **Inzozi AI Research** — planned.
4. **Inzozi AI Academia** — planned.
5. **Inzozi AI Automation** — planned.
6. **Inzozi AI Analytics** — planned.
7. **Inzozi AI Creative** — planned.

## Level 3 — Inzozi AI Coding

The application currently developed in this repository is **Inzozi AI Coding**. It must not present itself as though it is the whole Inzozi AI Solution or the whole Inzozi Digital platform.

Its interface should preserve this visible hierarchy:

`Inzozi Digital → Inzozi AI Solution → Inzozi AI Coding`

The existing Product Control, Engineering Space, guarded workspace, AI modes, repository tooling and Git delivery controls are capabilities of Inzozi AI Coding.

## Domain and routing principles

- `www.inzozidigital.com` is the parent platform/hub.
- `ai.inzozidigital.com` is the shared host for Inzozi AI products.
- AI product switching should remain inside the AI solution context rather than implying separate unrelated platforms.
- Other solution families will receive dedicated subdomains when activated.
- Product hierarchy and domain naming are separate from deployment authorization. UI changes must not automatically change DNS, TLS, Nginx, firewall or production routing.

## Current implementation boundary

Issue #98 introduces the frontend hierarchy foundation and the Inzozi AI Coding identity. It does **not** configure `www.inzozidigital.com`, create future solution subdomains, alter DNS/TLS, or deploy infrastructure.
