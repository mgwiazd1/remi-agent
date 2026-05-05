# DripStack OpenAPI Summary (fetched 2026-05-03)

Version: 0.1.0
Ownership: did:pkh:eip155:8453:0xBF22b6DdB5A08c823856A779f1004eEa60C5aB92

## Payment Protocols (Dual)
- MPP: Challenge via `WWW-Authenticate: Payment ...`, retry with `Authorization: Payment ...`
- x402 v2: Challenge via `PAYMENT-REQUIRED` header, retry with `PAYMENT-SIGNATURE` header
- 402 body: `application/problem+json` with {type, title, status, detail, challengeId}

## Dynamic Pricing
- OpenAPI metadata range: $0.05–$10.00 USD
- Runtime 402 challenge amount is AUTHORITATIVE (always trust over static metadata)

## Endpoints
1. GET /api/v1/publications — Free, returns PublicationListItem[]
2. GET /api/v1/publications/{publicationSlug} — Free, returns PublicationCore + PostSummary[]
3. POST /api/v1/publications/{publicationSlug} — Free, import/refresh, body: { forceRefresh?: bool }
4. GET /api/publications/posts — Free, paginated, requires publicationId OR publicationTitle
5. GET /api/v1/publications/{publicationSlug}/{postSlug} — PAID ($0.05–$10.00), 402 challenge

## Key Schemas
### PublicationListItem
{ slug (req), title?, description?, siteUrl (req), lastSyncedAt? }

### Full Post (after payment)
{ id, publicationId, publicationSlug, guid, slug, title, subtitle?, description?,
  url, author?, imageUrl?, publishedAt?, contentHtml?, createdAt, updatedAt }

## Agent Guidance Notes
- Always trust live 402 challenge over static OpenAPI pricing
- ?importer=1 on paid route redirects to web importer (NOT for API agents)
- SIWX bearer auth defined but NOT used — paid routes use x402/MPP only
