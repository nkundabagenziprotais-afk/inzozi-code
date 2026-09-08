# Public-staging ingress controls

These controls are release prerequisites for Issue #36. They do not authorize
public DNS, public ingress, or production deployment.

## Request budgets

Host Nginx applies source-IP budgets before proxying application traffic:

| Traffic | Sustained budget | Burst | Concurrent connections | Body limit |
| --- | ---: | ---: | ---: | ---: |
| Login `/api/v1/auth/login` | 12 requests/minute | 4 excess | 4 | 16 KiB |
| Expensive agent/workspace actions | 30 requests/minute | 5 excess | 4 | 64 KiB agent / 16 KiB actions |
| General `/api/` traffic | 15 requests/second | 45 excess | 16 | server default |
| Browser/static traffic | no request-rate budget | — | 32 | server default |

Nginx returns HTTP 429 for request-rate or connection-limit rejection.

The login budget is source-IP based rather than email based. This complements
the Redis application throttle, which is keyed by client IP plus normalized
email, and prevents simple email rotation from providing an unbounded PBKDF2
password-verification budget.

## Real-client-IP security boundary

The Nginx zones use `$binary_remote_addr`.

For current PRIVATE STAGING, the directly connected operator is the source.

Before a public reverse proxy/CDN is introduced, Issue #38 must establish an
authenticated trusted-proxy boundary so that `$remote_addr` is rewritten only
from the selected edge/provider and never from arbitrary public
`X-Real-IP`/`X-Forwarded-For` headers.

Do not open the origin to the internet merely to preserve client-IP rate
limits.

## Content Security Policy

The HTTPS host sends:

- `default-src 'self'`
- `script-src 'self'`
- `style-src 'self' 'unsafe-inline'`
- `img-src 'self' data: blob:`
- `font-src 'self' data:`
- `connect-src 'self'`
- `worker-src 'self' blob:`
- `child-src 'self' blob:`
- `object-src 'none'`
- `base-uri 'self'`
- `form-action 'self'`
- `frame-ancestors 'self'`
- `manifest-src 'self'`
- `upgrade-insecure-requests`

`style-src 'unsafe-inline'` is intentionally limited to styles because Monaco
and browser UI libraries may create runtime style elements. Script execution
does not permit `unsafe-inline` or `unsafe-eval`.

## Activation boundary

These controls must first pass:

1. local/static review;
2. PR CI on the exact reviewed SHA;
3. explicit merge authorization;
4. post-merge CI;
5. PRIVATE STAGING deployment/acceptance;
6. rotating-email login abuse probes;
7. body-size and connection/rate rejection tests;
8. normal login/session/workspace smoke tests.

Public DNS and public ingress remain separately authorized gates under Issue
#35 and Issue #38.
