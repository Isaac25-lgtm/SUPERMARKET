# Accounting policy (TEST configuration — requires review by the client's accountant)

| Topic | Policy in this system |
|---|---|
| Ledger | Tryton double-entry; posted entries immutable; corrections by reversal |
| Currency | UGX, rounded to whole shillings; `Decimal`/`NUMERIC` only |
| Inventory valuation | Perpetual ("continental"), **weighted-average cost** per product |
| COGS | Posted at sale time at the current average cost (5.1.1.01) |
| Purchases | Receipt: Dr Inventory / Cr Purchase clearing (5.1.1.02). Supplier bill: Dr Purchase clearing / Cr Payables. The clearing balance is a **timing item**, excluded from profit and shown separately. |
| Shrinkage | Write-offs/damage/transit losses → Lost & Found: Dr 5.1.1.03 Shrinkage / Cr Inventory (kept apart from COGS) |
| Opening stock | Dr Inventory / Cr 3.4.4.01 Opening-balance equity |
| Inter-branch transfers | Internal moves only: no journal entry, no revenue. Stock remains valued while in transit. |
| POS sale | Dr till cash or clearing (mobile money / card pending verification) / Cr revenue (ex-VAT) / Cr VAT output |
| Refund | Reverse entry of the original line prices; stock returns to the shop floor; COGS reversed at current cost |
| Cash variance | Booked as a cash transfer to 6.1.2.01 Cash over/short before the session closes |
| Expenses | **Accrual**: recognised when the approved bill is posted; payment is separate (Dr Payables / Cr Bank) |
| Branch dimension | `account.move.branch` on every entry; entries without one are "Head office / unallocated" |
| VAT | **ILLUSTRATIVE** 18% on selected categories, basic foodstuff without VAT. Not a statutory configuration; no EFRIS. |
| Income tax | Not modelled; "estimated net profit" is before tax |

## Report definitions
- **Net sales** = credits − debits on revenue accounts (4.*), net of refunds and discounts, excluding VAT.
- **Gross profit** = net sales − COGS.
- **Operating profit** = gross profit − shrinkage − operating expenses (5.2.*) − other operating (6.1.2.*).
- **Break-even sales** = fixed costs ÷ contribution-margin ratio, where variable costs = COGS + shrinkage, fixed
  costs = posted operating expenses, and the margin is blended across the product mix (assumptions are displayed).
- **Capital recovery** = cumulative net movement on cash & cash-equivalent accounts since the first investment
  date. It excludes equity (owner) moves and transfers between cash accounts, and is **not** accounting profit. A
  projection requires ≥ 8 weeks of history.

## Sample reconciliations (automated in `api/tests`)
- Every journal entry balances, and the trial balance balances.
- Σ branch P&L lines = consolidated, for every period.
- P&L net sales and gross profit equal an independent SQL computation over posted lines.
- A 3-unit VAT sale posts exactly cash = gross, revenue = round(3 × price ÷ 1.18), VAT = remainder, and COGS =
  3 × average cost.
- The 100 → 95 transfer conserves quantity across origin + transit + destination and creates no journal entry.
