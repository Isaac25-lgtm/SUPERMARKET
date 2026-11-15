# Architecture & decision record

## ADR-1 — Tryton 8.0 LTS as the business engine
**Decision.** Use Tryton 8.0 LTS (pinned 8.0.14 + official 8.0 modules) as the system of record for products,
stock, POS (`sale_point`), purchasing, invoices and the double-entry ledger.
**Why.** Python + PostgreSQL natively; mature double-entry accounting with immutable posted moves; perpetual
("continental") stock valuation; strict gap-free sequences for receipts and journal entries; an LTS series with
security releases. ERPNext was rejected because it requires MariaDB.
**Not rewritten.** We do not implement a parallel ledger, stock engine or tax engine. All postings go through
Tryton workflows (`sale.point.sale.process/post`, `stock.shipment.internal.wait/assign/pack/ship/do`,
`purchase.quote/confirm/process`, `stock.shipment.in.receive/do`, `account.invoice.post`, `pay_invoice`).

## ADR-2 — In-process engine behind a FastAPI policy layer
Tryton runs **in the API process** (`api/motion_api/tryton.py`). Its RPC server is not exposed, so the only way into
business data is through the policy layer.
- **Authentication & authorization** are enforced by FastAPI (`security.py`, `permissions.py`): role permissions
  combined with explicit branch scope on every endpoint and every resource fetched by id.
- **Attribution.** Every staff account has a password-less Tryton `res.user` + employee. Business work runs under
  that user, so Tryton's own `create_uid`/`write_uid` record who did what.
- **One transaction per request.** The platform's own tables (schema `motion`) are written through the *same*
  connection as the Tryton work, so sale + stock + journal entry + idempotency row + audit event are atomic.
- **Concurrency.** Tryton runs at REPEATABLE READ. The gateway retries on serialization failures, implements
  Tryton's lock-escalation protocol, and *learns* per-operation lock needs (strict sequences), so checkout does not
  restart. POS oversell protection updates a `(location, product)` guard row first, which makes concurrent sales
  of the same product conflict and retry with a fresh snapshot.
- **Silent-skip protection.** Tryton workflow transitions silently ignore records in an invalid state. Every
  transition the platform drives is followed by `expect_state(...)`, which raises and rolls back.

## ADR-3 — Branch dimension inside the ledger (`tryton-modules/motion_branch`)
A custom Tryton module adds `motion.branch` (linked to a warehouse) and a `branch` field on `account.move`.
It is stamped by POS sales, cash transfers, continental stock moves (from the storage location's warehouse, or the
`motion_branch` context for transit) and invoices (from the purchase warehouse or the expense's branch). A
context default covers upstream moves such as invoice payments. Branch P&L is therefore a GROUP BY that reconciles
to consolidated by construction (tested). The module also routes Lost & Found moves to a separate shrinkage
account, and opening stock to opening-balance equity.

## ADR-4 — PostgreSQL / Neon
One database: Tryton tables in `public`, platform tables in `motion`. Bootstrap pins the database `search_path` to
`public`, so placement doesn't depend on the role name. Neon's `-pooler` host is swapped for the direct endpoint
(Tryton keeps its own pool). Cache invalidation is poll-based (`clean_timeout`), so no LISTEN/NOTIFY is needed.

## ADR-5 — Custom React UI
End users never see the Tryton client. The React app is served by FastAPI on the same origin, so cookie auth works
without CORS. The UI only *hides* what a role can't do; the server enforces.

## ADR-6 — Offline isolation (`branch-edge/`)
A browser-only offline store cannot coordinate several tills on one LAN. Each branch therefore runs a small edge
server instead:
- **Storage.** SQLite in WAL mode with `synchronous=FULL`, and `BEGIN IMMEDIATE` per sale.
- **Outbox.** Immutable outbox events, enforced by triggers.
- **Sync.** Idempotent push with backoff, then pull of a snapshot.

The central system ingests each event in its own transaction, keyed by UUID (exactly once), through the same POS
posting path. Cashier grants expire, which bounds how long a revoked account could keep selling offline.

## ADR-7 — AI is assistive
Alerts are rule-based (`rules-v1`), computed from read-only, branch-scoped data, with evidence attached.

**Ask Motion AI** (`services/ai.py`, `POST /api/ai/chat`) is a tool-calling loop against DeepSeek's
OpenAI-compatible API:

1. The request is authenticated and CSRF-checked in a short transaction. The provider call then happens outside
   any database transaction.
2. The model sees only a system prompt (brand, date, the user's roles, visible branches and the app sections they
   may open, plus grounding rules) and the read-only tools **their role allows** (`TOOL_ACCESS`). No SQL and no
   write tools exist. Each answer returns links to the sections behind it, filtered by the same route permissions
   the UI enforces (`SECTIONS`).
3. Each tool call runs in its own read-only transaction, through the same service functions and permission and
   branch checks as the normal API. A denial goes back to the model as `{"error": ...}`.
4. Results are trimmed to aggregates; per-cashier figures and cost prices are excluded. History is capped at
   8 turns × 2,000 characters, with up to 5 tool rounds and a 45 s timeout.
5. Each question is logged in `motion.ai_query` (question, tools, answer, model, tokens, latency, status), audited
   as `ai.ask`, and rate-limited per user per hour. If the provider fails, the rule-based answer is shown.

Suggestions only link to draft forms, and every action still goes through the normal approval workflow.

## Request flow (checkout)
```text
POST /api/pos/sales ─► unit_of_work: session cookie → Principal; CSRF; perm pos.sell; branch scope
   └► Tryton transaction (as cashier's res.user):
        idempotency check → lock stock guards → stock check → price & discount policy → override check
        → sale.point.sale(lines, payments) → process (strict receipt no.) → post (stock moves + JE + COGS JEs)
        → motion.pos_sale(+lines) → audit_event           ── all commit together
```
