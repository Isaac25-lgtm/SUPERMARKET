"""Operational base configuration (idempotent; NOT demo data).

Creates, only when missing: UGX currency, the company, the chart of accounts
(Tryton "Universal Chart of Accounts" template + supermarket accounts),
fiscal years with continental (perpetual) stock valuation, journals,
ILLUSTRATIVE VAT taxes, POS payment methods, cash-transfer types, product
accounting categories, branches/warehouses/registers from config/chain.json,
platform roles and EXAMPLE approval thresholds.

Tax note: the 18% VAT rate created here is ILLUSTRATIVE only. It is not a
verified statutory configuration and there is no URA/EFRIS integration.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from decimal import Decimal
from pathlib import Path

from . import tryton as t
from .permissions import ROLES
from .settings import Settings

logger = logging.getLogger("motion.setup")
CONFIG = json.loads((Path(__file__).parent / "config" / "chain.json").read_text(encoding="utf-8"))

# Supermarket-specific accounts: (code, name, parent template code, type key, reconcile)
# type keys refer to account types resolved/created in _account_types().
ACCOUNTS = [
    ("1.1.1.01", "Till cash (all POS registers)", "1.1.1", "cash", False),
    ("1.1.1.02", "Main safe / cash office", "1.1.1", "cash", False),
    ("1.1.1.03", "Bank - operating account (TEST)", "1.1.1", "cash", False),
    ("1.1.1.04", "Mobile money receipts - pending verification", "1.1.1", "cash", False),
    ("1.1.1.05", "Card receipts - pending settlement", "1.1.1", "cash", False),
    ("1.3.1.01", "Merchandise inventory", "1.3.1", "inventory", False),
    ("1.4.1.01", "VAT input recoverable (ILLUSTRATIVE)", "1.4.1", "prepaid", False),
    ("2.1.1.01", "Trade payables - suppliers", "2.1.1", "payable", True),
    ("2.2.3.01", "VAT output payable (ILLUSTRATIVE)", "2.2.3", "tax_liability", False),
    ("3.1.1.01", "Owner capital", "3.1.1", "equity", False),
    ("3.4.4.01", "Opening balance equity", "3.4.4", "equity", False),
    ("4.1.1.01", "Sales - goods", "4.1.1", "revenue", False),
    ("5.1.1.01", "Cost of goods sold", "5.1.1", "cogs", False),
    ("5.1.1.02", "Purchase clearing - goods received", "5.1.1", "purchase_clearing", False),
    ("5.1.1.03", "Stock shrinkage, damage & write-offs", "5.1.1", "shrinkage", False),
    ("5.2.2.01", "Rent", "5.2.2", "opex", False),
    ("5.2.2.02", "Salaries & wages", "5.2.2", "opex", False),
    ("5.2.2.03", "Electricity & water", "5.2.2", "opex", False),
    ("5.2.2.04", "Transport & logistics", "5.2.2", "opex", False),
    ("5.2.2.05", "Repairs & maintenance", "5.2.2", "opex", False),
    ("5.2.2.06", "Marketing", "5.2.2", "opex", False),
    ("5.2.2.07", "Security & cleaning", "5.2.2", "opex", False),
    ("5.2.2.08", "Other operating expenses", "5.2.2", "opex", False),
    ("6.1.2.01", "Cash over / short", "6.1.2", "other_expense", False),
]
EXPENSE_CATEGORIES = {
    "rent": "5.2.2.01", "salaries": "5.2.2.02", "utilities": "5.2.2.03",
    "transport": "5.2.2.04", "repairs": "5.2.2.05", "marketing": "5.2.2.06",
    "security_cleaning": "5.2.2.07", "other": "5.2.2.08",
}
PRODUCT_CATEGORIES = [
    ("Foodstuff - dry & bulk", False), ("Fresh produce", False), ("Meat & fish", False),
    ("Dairy & bakery", False), ("Cooking oil & spreads", False), ("Groceries - packaged", True),
    ("Beverages", True), ("Household", True), ("Personal care", True),
]  # (name, VAT applies). ILLUSTRATIVE ONLY: basic foodstuffs are commonly exempt/zero-rated,
#   but the real treatment per item must be confirmed by the client's tax advisor.


def _md(module: str, xml_id: str) -> int:
    return t.model("ir.model.data").get_id(module, xml_id)


def _company():
    Company = t.model("company.company")
    companies = Company.search([], limit=1)
    return companies[0] if companies else None


def _ensure_currency():
    Currency = t.model("currency.currency")
    found = Currency.search([("code", "=", "UGX")])
    if found:
        return found[0]
    cur, = Currency.create([{
        "name": "Ugandan Shilling", "code": "UGX", "numeric_code": "800", "symbol": "USh",
        "rounding": Decimal("1"), "digits": 0,
    }])
    return cur


def _ensure_company(settings: Settings, currency):
    company = _company()
    if company:
        return company
    Party = t.model("party.party")
    Company = t.model("company.company")
    User = t.model("res.user")
    party, = Party.create([{"name": settings.company_name}])
    company, = Company.create([{"party": party.id, "currency": currency.id, "timezone": settings.timezone}])
    admin = User(1)
    User.write([admin], {"companies": [("add", [company.id])], "company": company.id})
    logger.info("created company %s", settings.company_name)
    return company


def _ensure_chart(company):
    Account = t.model("account.account")
    if Account.search([("company", "=", company.id)], limit=1):
        return
    AccTemplate = t.model("account.account.template")
    root = AccTemplate(_md("account", "account_template_root_en"))
    with t.set_context(company=company.id, language="en"):
        template2type: dict = {}
        root.type.create_type(company.id, template2type=template2type)
        template2account: dict = {}
        root.create_account(company.id, template2account=template2account, template2type=template2type)
    logger.info("created Universal chart of accounts")


def _account_types(company) -> dict[str, object]:
    Type = t.model("account.account.type")

    def find(name):
        found = Type.search([("company", "=", company.id), ("name", "=", name)], limit=1)
        return found[0] if found else None

    def ensure(name, parent_name, **flags):
        found = find(name)
        if found:
            return found
        parent = find(parent_name)
        ty, = Type.create([{"name": name, "company": company.id, "parent": parent.id if parent else None,
                             "statement": flags.pop("statement"), **flags}])
        return ty

    return {
        "cash": find("Cash and Cash Equivalents"),
        "inventory": find("Inventories"),
        "prepaid": find("Prepaid expenses for future services that will be used within a year"),
        "payable": find("Accounts payable"),
        "receivable": find("Accounts receivable"),
        "tax_liability": find("Deferred tax liabilities and deferred tax assets")
        or find("Liabilities and assets for current tax"),
        "equity": find("Issued capital and reserves attributable to equity holders of the parent company (controlling interest)"),
        "revenue": find("Revenue"),
        "cogs": ensure("Cost of goods sold (perpetual)", "Cost of Sales",
                       statement="income", expense=True, stock=True),
        "purchase_clearing": ensure("Purchase clearing (perpetual)", "Cost of Sales",
                                    statement="income", expense=True, stock=True),
        "shrinkage": ensure("Shrinkage & write-offs", "Cost of Sales", statement="income", expense=True),
        "opex": ensure("Operating expenses", "Expenses", statement="income", expense=True),
        "other_expense": ensure("Other operating losses", "Other expenses or losses",
                                statement="income", expense=True),
    }


def _account(company, code):
    Account = t.model("account.account")
    found = Account.search([("company", "=", company.id), ("code", "=", code)], limit=1)
    return found[0] if found else None


def _ensure_accounts(company):
    Account = t.model("account.account")
    types = _account_types(company)
    missing = [k for k, v in types.items() if v is None]
    if missing:
        raise RuntimeError(f"account types not found in chart: {missing}")
    for code, name, parent_code, type_key, reconcile in ACCOUNTS:
        if _account(company, code):
            continue
        parent = _account(company, parent_code)
        # Tryton: a child account's type must equal its parent's type when the
        # parent has one; climb to the nearest compatible ancestor.
        while parent and parent.type and parent.type != types[type_key]:
            parent = parent.parent
        Account.create([{"code": code, "name": name, "company": company.id,
                         "parent": parent.id if parent else None,
                         "type": types[type_key].id, "reconcile": reconcile,
                         "party_required": type_key in {"payable", "receivable"}}])
    # Tryton requires receivable/payable defaults for parties.
    Config = t.model("account.configuration")
    config = Config(1)
    if not config.default_account_payable:
        receivable = _account(company, "1.2.1")
        if not receivable.party_required:
            Account.write([receivable], {"party_required": True})
        config.default_account_payable = _account(company, "2.1.1.01")
        config.default_account_receivable = _account(company, "1.2.1")
        config.save()
    MConfig = t.model("motion.configuration")
    mconfig = MConfig(1)
    if not mconfig.shrinkage_account:
        mconfig.shrinkage_account = _account(company, "5.1.1.03")
        mconfig.save()


def _ensure_fiscal_years(company):
    FiscalYear = t.model("account.fiscalyear")
    SequenceStrict = t.model("ir.sequence.strict")
    current = dt.date.today().year
    for year in sorted(set(CONFIG["fiscal_years"]) | {current, current + 1}):
        if FiscalYear.search([("company", "=", company.id), ("name", "=", str(year))]):
            continue
        move_seq, = SequenceStrict.create([{
            "name": f"Journal entries {year}", "company": company.id,
            "sequence_type": _md("account", "sequence_type_account_move"), "prefix": f"JE{year}-", "padding": 6}])
        inv = {}
        for key, prefix in (("in_invoice_sequence", "SINV"), ("in_credit_note_sequence", "SCN"),
                            ("out_invoice_sequence", "INV"), ("out_credit_note_sequence", "CN")):
            seq, = SequenceStrict.create([{
                "name": f"{prefix} {year}", "company": company.id,
                "sequence_type": _md("account_invoice", "sequence_type_account_invoice"),
                "prefix": f"{prefix}{year}-", "padding": 5}])
            inv[key] = seq.id
        fy, = FiscalYear.create([{
            "name": str(year), "company": company.id,
            "start_date": dt.date(year, 1, 1), "end_date": dt.date(year, 12, 31),
            "move_sequence": move_seq.id, "account_stock_method": "continental",
            "invoice_sequences": [("create", [{"company": company.id, **inv}])],
        }])
        FiscalYear.create_period([fy])
        logger.info("created fiscal year %s with monthly periods", year)


def _ensure_taxes(company):
    Tax = t.model("account.tax")
    name = "VAT 18% (ILLUSTRATIVE)"
    found = Tax.search([("company", "=", company.id), ("name", "=", name)])
    if found:
        return found[0]
    out_acc = _account(company, "2.2.3.01")
    in_acc = _account(company, "1.4.1.01")
    sale_tax, purchase_tax = Tax.create([
        {"name": name, "description": "VAT 18% illustrative - verify with tax advisor", "company": company.id,
         "type": "percentage", "rate": Decimal("0.18"),
         "invoice_account": out_acc.id, "credit_note_account": out_acc.id},
        {"name": "Input VAT 18% (ILLUSTRATIVE)", "description": "Input VAT illustrative", "company": company.id,
         "type": "percentage", "rate": Decimal("0.18"),
         "invoice_account": in_acc.id, "credit_note_account": in_acc.id},
    ])
    return sale_tax


def _ensure_product_categories(company):
    Category = t.model("product.category")
    Tax = t.model("account.tax")
    sale_tax = Tax.search([("company", "=", company.id), ("name", "=", "VAT 18% (ILLUSTRATIVE)")])[0]
    purchase_tax = Tax.search([("company", "=", company.id), ("name", "=", "Input VAT 18% (ILLUSTRATIVE)")])[0]
    accounts = {
        "account_revenue": _account(company, "4.1.1.01").id,
        "account_expense": _account(company, "5.1.1.02").id,
        "account_stock": _account(company, "1.3.1.01").id,
        "account_stock_in": _account(company, "5.1.1.02").id,
        "account_stock_out": _account(company, "5.1.1.01").id,
    }
    for name, vat in PRODUCT_CATEGORIES:
        if Category.search([("name", "=", name)]):
            continue
        values = {"name": name, "accounting": True, **accounts}
        if vat:
            values["customer_taxes"] = [("add", [sale_tax.id])]
            values["supplier_taxes"] = [("add", [purchase_tax.id])]
        with t.set_context(company=company.id):
            Category.create([values])


def _ensure_payment_methods(company):
    Method = t.model("sale.point.payment.method")
    TransferType = t.model("sale.point.cash.transfer.type")
    specs = [
        ("Cash", "1.1.1.01", True),
        ("Mobile money (pending verification)", "1.1.1.04", False),
        ("Card (pending settlement)", "1.1.1.05", False),
    ]
    for name, code, cash in specs:
        if not Method.search([("company", "=", company.id), ("name", "=", name)]):
            Method.create([{"company": company.id, "name": name, "account": _account(company, code).id, "cash": cash}])
    for name, code in (("Cash over / short", "6.1.2.01"), ("Cash drop to safe", "1.1.1.02"),
                       ("Float top-up from safe", "1.1.1.02"), ("Bank deposit", "1.1.1.03")):
        if not TransferType.search([("company", "=", company.id), ("name", "=", name)]):
            TransferType.create([{"company": company.id, "name": name, "account": _account(company, code).id,
                                  "journal": _md("account", "journal_cash")}])
    InvMethod = t.model("account.invoice.payment.method")
    Journal = t.model("account.journal")
    cash_journal = Journal(_md("account", "journal_cash"))
    for name, code in (("Bank transfer (TEST bank)", "1.1.1.03"), ("Cash from safe", "1.1.1.02")):
        if not InvMethod.search([("company", "=", company.id), ("name", "=", name)]):
            acc = _account(company, code)
            InvMethod.create([{"company": company.id, "name": name, "journal": cash_journal.id,
                               "debit_account": acc.id, "credit_account": acc.id}])


def _ensure_branches(company):
    Location = t.model("stock.location")
    Branch = t.model("motion.branch")
    Point = t.model("sale.point")
    Journal = t.model("account.journal")
    SequenceStrict = t.model("ir.sequence.strict")
    lost_found = Location(_md("stock", "location_lost_found"))
    customer = Location(_md("stock", "location_customer"))
    default_wh = Location(_md("stock", "location_warehouse"))
    for idx, spec in enumerate(CONFIG["branches"]):
        branch = Branch.search([("code", "=", spec["code"])])
        if branch:
            branch = branch[0]
        else:
            if idx == 0 and not Branch.search([("warehouse", "=", default_wh.id)]):
                wh = default_wh
                Location.write([wh], {"name": f"{spec['name']} warehouse", "code": spec["code"]})
                Location.write([wh.storage_location], {"name": f"{spec['code']} shop floor & store"})
            else:
                inp, out, sto = Location.create([
                    {"name": f"{spec['code']} receiving", "type": "storage"},
                    {"name": f"{spec['code']} dispatch", "type": "storage"},
                    {"name": f"{spec['code']} shop floor & store", "type": "storage"},
                ])
                wh, = Location.create([{
                    "name": f"{spec['name']} warehouse", "code": spec["code"], "type": "warehouse",
                    "input_location": inp.id, "output_location": out.id, "storage_location": sto.id,
                    "lost_found_location": lost_found.id}])
                Location.write([inp, out, sto], {"parent": wh.id})
            branch, = Branch.create([{"code": spec["code"], "name": spec["name"],
                                      "company": company.id, "warehouse": wh.id}])
            logger.info("created branch %s", spec["code"])
        journal = Journal.search([("code", "=", f"POS-{spec['code']}")])
        if not journal:
            journal = Journal.create([{"name": f"POS sales {spec['name']}", "code": f"POS-{spec['code']}",
                                       "type": "revenue"}])
        journal = journal[0]
        for reg in spec["registers"]:
            existing = Point.search([("name", "=", reg)])
            if existing:
                # Returned goods go back to the shop floor (Tryton has no fallback in code).
                if not existing[0].return_location:
                    Point.write(existing, {"return_location": branch.warehouse.storage_location.id})
                continue
            seq, = SequenceStrict.create([{
                "name": f"Receipts {reg}", "company": company.id,
                "sequence_type": _md("sale_point", "sequence_type_sale"), "prefix": f"{reg}-", "padding": 6}])
            Point.create([{
                "name": reg, "company": company.id, "tax_included": True, "sequence": seq.id,
                "storage_location": branch.warehouse.storage_location.id,
                "return_location": branch.warehouse.storage_location.id,
                "customer_location": customer.id, "journal": journal.id, "branch": branch.id}])


def _ensure_platform_rows():
    for r in ROLES:
        t.execute(
            "INSERT INTO motion.role (code, name, description, rank) VALUES (%s,%s,%s,%s) "
            "ON CONFLICT (code) DO UPDATE SET name=EXCLUDED.name, description=EXCLUDED.description, rank=EXCLUDED.rank",
            (r.code, r.name, r.description, r.rank))
    if t.sql_one("SELECT 1 AS x FROM motion.approval_policy LIMIT 1"):
        return
    ops = Decimal(CONFIG["example_approval_thresholds_ugx"]["ops_limit"])
    gm = Decimal(CONFIG["example_approval_thresholds_ugx"]["gm_limit"])
    S = lambda role, label, kind="approve": {"role": role, "label": label, "kind": kind}  # noqa: E731
    policies = [
        ("purchase_request", Decimal(0), ops, [S("operations_manager", "Operations assessment", "review"),
                                               S("accountant", "Finance verification", "verify")]),
        ("purchase_request", ops + 1, gm, [S("operations_manager", "Operations assessment", "review"),
                                           S("accountant", "Finance verification", "verify"),
                                           S("general_manager", "General Manager approval")]),
        ("purchase_request", gm + 1, None, [S("operations_manager", "Operations assessment", "review"),
                                            S("accountant", "Finance verification", "verify"),
                                            S("general_manager", "General Manager recommendation", "review"),
                                            S("owner", "CFO / Head Manager final approval")]),
        ("expense", Decimal(0), gm, [S("accountant", "Finance verification", "verify"),
                                     S("general_manager", "General Manager approval")]),
        ("expense", gm + 1, None, [S("accountant", "Finance verification", "verify"),
                                   S("general_manager", "General Manager recommendation", "review"),
                                   S("owner", "CFO / Head Manager final approval")]),
        ("supplier_payment", Decimal(0), gm, [S("general_manager", "Payment approval")]),
        ("supplier_payment", gm + 1, None, [S("general_manager", "Payment recommendation", "review"),
                                            S("owner", "CFO / Head Manager payment approval")]),
        ("transfer", Decimal(0), None, [S("operations_manager", "Operations approval")]),
        ("refund", Decimal(0), Decimal(100000), [S("branch_manager", "Branch Manager approval")]),
        ("refund", Decimal(100001), None, [S("branch_manager", "Branch Manager review", "review"),
                                           S("general_manager", "General Manager approval")]),
        ("stock_writeoff", Decimal(0), ops, [S("operations_manager", "Operations approval")]),
        ("stock_writeoff", ops + 1, None, [S("operations_manager", "Operations review", "review"),
                                           S("accountant", "Finance verification", "verify"),
                                           S("general_manager", "General Manager approval")]),
        ("price_override", Decimal(0), None, [S("branch_manager", "Manager override at till")]),
    ]
    for action, lo, hi, steps in policies:
        t.execute(
            "INSERT INTO motion.approval_policy (action_type, min_amount, max_amount, steps, note) "
            "VALUES (%s,%s,%s,%s::jsonb,%s)",
            (action, lo, hi, json.dumps(steps), "EXAMPLE threshold seeded for demo/UAT - not client-approved"))
    t.execute(
        "INSERT INTO motion.app_setting (key, value) VALUES ('pos_policy', %s::jsonb) ON CONFLICT DO NOTHING",
        (json.dumps({"cashier_max_discount_pct": 5, "allow_negative_stock": False,
                     "receipt_footer": "Thank you for shopping with us"}),))


def ensure(settings: Settings) -> None:
    t.configure(settings)

    def work():
        # Foodstuff sold by weight: scale labels resolve to the gram.
        UoM = t.model("product.uom")
        kg = UoM(_md("product", "uom_kilogram"))
        if kg.digits != 3:
            UoM.write([kg], {"rounding": 0.001, "digits": 3})
        currency = _ensure_currency()
        company = _ensure_company(settings, currency)
        with t.set_context(company=company.id):
            _ensure_chart(company)
            _ensure_accounts(company)
            _ensure_fiscal_years(company)
            _ensure_taxes(company)
            _ensure_product_categories(company)
            _ensure_payment_methods(company)
            _ensure_branches(company)
        _ensure_platform_rows()

    t.run(work, user=0, context={"language": "en"})
    logger.info("base setup verified")
