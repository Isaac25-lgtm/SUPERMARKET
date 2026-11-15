# Licences & dependency inventory

| Component | Licence | Notes |
|---|---|---|
| trytond 8.0.14 and official modules (company, currency, country, party, product, stock, account, account_product, account_invoice, account_invoice_stock, account_stock_continental, sale, purchase, sale_point) | **GPL-3.0-or-later** | installed unmodified from PyPI |
| `tryton-modules/motion_branch` (this repo) | **GPL-3.0-or-later** | derivative Tryton module; LICENSE included |
| python-sql | BSD-3-Clause | Tryton dependency |
| FastAPI, Starlette, Pydantic, Uvicorn, httpx | MIT / BSD-3-Clause | |
| psycopg 3, psycopg-pool | LGPL-3.0 | |
| argon2-cffi | MIT | |
| React, React Router, TanStack Query, React Hook Form, Zod, Recharts, Radix UI, Lucide, clsx, Tailwind CSS, Vite | MIT / ISC | front-end bundle |

## Obligations
- If this system is **distributed** to a client (e.g. installed on their servers or branch PCs), the GPL requires
  that the corresponding source of GPL components, including `motion_branch` and any module linked into the Tryton
  process, be made available to that client under the GPL. White-labelling does not remove this obligation.
- The FastAPI policy layer imports Tryton in-process. Treat the combined API process as subject to the GPL when
  distributing it, and obtain legal review before relying on any other interpretation.
- Keep upstream copyright notices. The pinned versions are recorded in `requirements.txt`.
- This inventory is a technical summary, not legal advice.
