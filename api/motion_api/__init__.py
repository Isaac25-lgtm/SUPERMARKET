"""Motion Imprints — supermarket operations platform API (backend-for-frontend).

FastAPI is the policy-enforcement point (authentication, role/branch scoping,
approvals, audit).  Tryton 8.0 LTS, running in-process, is the system of record
for products, stock, POS, purchasing, invoices and the double-entry ledger.
"""

__version__ = "0.1.0"
