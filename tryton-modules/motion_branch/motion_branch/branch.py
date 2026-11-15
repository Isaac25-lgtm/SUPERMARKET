# Licensed GPL-3.0-or-later — part of the Motion Imprints Tryton extensions.
from trytond.model import (
    DeactivableMixin, ModelSingleton, ModelSQL, ModelView, Unique, fields)
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.transaction import Transaction


class Branch(DeactivableMixin, ModelSQL, ModelView):
    "Supermarket branch / outlet"
    __name__ = 'motion.branch'
    _rec_name = 'name'

    code = fields.Char("Code", required=True, size=12)
    name = fields.Char("Name", required=True)
    company = fields.Many2One('company.company', "Company", required=True)
    warehouse = fields.Many2One(
        'stock.location', "Warehouse", required=True, ondelete='RESTRICT',
        domain=[('type', '=', 'warehouse')])

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('code_unique', Unique(t, t.company, t.code),
                'motion_branch.msg_branch_code_unique'),
            ('warehouse_unique', Unique(t, t.warehouse),
                'motion_branch.msg_branch_warehouse_unique'),
            ]
        cls._order.insert(0, ('code', 'ASC'))

    @classmethod
    def default_company(cls):
        return Transaction().context.get('company')

    @classmethod
    def for_location(cls, location):
        "Return the branch owning a stock location (or None)."
        if not location:
            return None
        warehouse = location.warehouse
        if not warehouse:
            return None
        branches = cls.search([('warehouse', '=', warehouse.id)], limit=1)
        return branches[0] if branches else None

    @classmethod
    def from_context(cls):
        branch_id = Transaction().context.get('motion_branch')
        return cls(branch_id) if branch_id else None


class Configuration(ModelSingleton, ModelSQL, ModelView):
    "Motion accounting configuration"
    __name__ = 'motion.configuration'

    shrinkage_account = fields.Many2One(
        'account.account', "Stock Shrinkage Account",
        domain=[('type', '!=', None), ('closed', '!=', True)],
        help="Expense account used for stock written off to or recovered "
        "from Lost and Found, so shrinkage is reported separately from COGS.")


class SalePoint(metaclass=PoolMeta):
    __name__ = 'sale.point'
    branch = fields.Many2One(
        'motion.branch', "Branch", ondelete='RESTRICT',
        domain=[('company', '=', Eval('company', -1))])


class SalePointSale(metaclass=PoolMeta):
    __name__ = 'sale.point.sale'

    def get_account_move(self):
        move = super().get_account_move()
        move.branch = self.point.branch
        return move


class CashTransfer(metaclass=PoolMeta):
    __name__ = 'sale.point.cash.transfer'

    def get_move(self, account):
        move = super().get_move(account)
        move.branch = self.point.branch
        return move


class AccountMove(metaclass=PoolMeta):
    __name__ = 'account.move'
    branch = fields.Many2One(
        'motion.branch', "Branch", ondelete='RESTRICT',
        states={'readonly': Eval('state') == 'posted'})

    @classmethod
    def default_branch(cls):
        # Moves built by upstream code we do not override (e.g. invoice
        # payments) inherit the branch of the unit of work that created them.
        return Transaction().context.get('motion_branch')


class StockMove(metaclass=PoolMeta):
    __name__ = 'stock.move'

    def _motion_branch(self):
        pool = Pool()
        Branch = pool.get('motion.branch')
        branch = Branch.from_context()
        if branch:
            return branch
        for location in (self.from_location, self.to_location):
            if location and location.type == 'storage':
                branch = Branch.for_location(location)
                if branch:
                    return branch
        return None

    def _get_account_stock_move_lines(self, type_):
        lines = super()._get_account_stock_move_lines(type_)
        if type_ in {'out_lost_found', 'in_lost_found'}:
            Account = Pool().get('account.account')
            config = Pool().get('motion.configuration')(1)
            # Opening stock is booked against opening-balance equity, not
            # shrinkage; the caller sets this context explicitly.
            counterpart = Transaction().context.get(
                'motion_lost_found_account')
            if counterpart:
                for line in lines:
                    line.account = Account(counterpart)
            elif config.shrinkage_account:
                for line in lines:
                    line.account = config.shrinkage_account
        return lines

    def _get_account_stock_move(self):
        move = super()._get_account_stock_move()
        if move:
            move.branch = self._motion_branch()
        return move


class Invoice(metaclass=PoolMeta):
    __name__ = 'account.invoice'
    branch = fields.Many2One(
        'motion.branch', "Branch", ondelete='RESTRICT',
        states={'readonly': Eval('state') != 'draft'})

    def get_move(self):
        move = super().get_move()
        if move and self.branch:
            move.branch = self.branch
        return move


class Purchase(metaclass=PoolMeta):
    __name__ = 'purchase.purchase'

    def _get_invoice(self):
        invoice = super()._get_invoice()
        Branch = Pool().get('motion.branch')
        invoice.branch = Branch.for_location(self.warehouse) if (
            self.warehouse) else None
        return invoice
