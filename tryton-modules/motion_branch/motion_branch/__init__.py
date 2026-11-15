# Motion Imprints — branch dimension for Tryton 8.0.
# Licensed GPL-3.0-or-later (same as Tryton) — see LICENSE in this directory.
"""Adds a first-class *branch* dimension to the Tryton ledger.

Every account.move created by the flows the platform uses (point-of-sale
posting, POS cash transfers, continental stock valuation moves, supplier and
expense invoices) is stamped with the branch it belongs to.  Branch P&L is
therefore a plain GROUP BY on posted ledger lines and always reconciles to the
consolidated ledger (moves without a branch are reported as "Head office /
unallocated").
"""
