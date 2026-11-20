from odoo import Command
from odoo.tests import TransactionCase


class FieldSalesCase(TransactionCase):
    """Reusable P01 fixtures for subsequent visit planner phases."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env['res.company']
        cls.company_a = Company.create({'name': 'Field Sales Company A'})
        cls.company_b = Company.create({'name': 'Field Sales Company B'})
        Partner = cls.env['res.partner']
        cls.shared_customer = Partner.create({'name': 'Shared Field Customer'})
        cls.customer_a = Partner.create({'name': 'Field Customer A', 'company_id': cls.company_a.id})
        cls.customer_b = Partner.create({'name': 'Field Customer B', 'company_id': cls.company_b.id})
        cls.salesperson_a = cls._user('field_sales_a', cls.company_a, 'group_field_salesperson')
        cls.salesperson_b = cls._user('field_sales_b', cls.company_b, 'group_field_salesperson')
        cls.manager = cls._user('field_sales_manager', cls.company_a, 'group_sales_manager')
        cls.app_admin = cls._user('field_sales_admin', cls.company_a, 'group_field_sales_admin')
        cls.unauthorized = cls._user('field_sales_unauthorized', cls.company_a, None)
        cls.territory_a = cls._territory('A', cls.company_a)
        cls.territory_b = cls._territory('B', cls.company_b)

    @classmethod
    def _user(cls, login, company, group):
        groups = [cls.env.ref('base.group_user').id]
        if group:
            groups.append(cls.env.ref('gt_field_sales_visit_planner.' + group).id)
        return cls.env['res.users'].with_context(no_reset_password=True).create({
            'name': login, 'login': login, 'email': login + '@example.test',
            'company_id': company.id, 'company_ids': [Command.set([company.id])],
            'group_ids': [Command.set(groups)],
        })

    @classmethod
    def _territory(cls, code, company):
        return cls.env['gt.sales.territory'].with_company(company).create({
            'name': 'Territory ' + code, 'code': code, 'company_id': company.id,
        })

    def assignment_vals(self, territory=None, user=None, **extra):
        territory = territory or self.territory_a
        vals = {'territory_id': territory.id, 'company_id': territory.company_id.id,
                'user_id': (user or self.salesperson_a).id, 'date_from': '2026-01-01'}
        vals.update(extra)
        return vals

    def allocation_vals(self, territory=None, partner=None, **extra):
        territory = territory or self.territory_a
        vals = {'territory_id': territory.id, 'company_id': territory.company_id.id,
                'partner_id': (partner or self.shared_customer).id}
        vals.update(extra)
        return vals
