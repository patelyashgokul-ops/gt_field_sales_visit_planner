from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import FieldSalesCase


@tagged('post_install', '-at_install')
class TestFoundation(FieldSalesCase):

    def test_territory_permissions_and_code(self):
        Territory = self.env['gt.sales.territory']
        with self.assertRaises((AccessError, ValidationError)):
            Territory.with_user(self.unauthorized).create({'name': 'No', 'code': 'N', 'company_id': self.company_a.id})
        with self.assertRaises((AccessError, ValidationError)):
            self.territory_a.with_user(self.salesperson_a).write({'name': 'Changed'})
        with self.assertRaises(Exception), self.cr.savepoint():
            self._territory('A', self.company_a)
        self.assertTrue(self.territory_a.with_user(self.manager).exists())

    def test_assignment_dates_overlap_and_eligibility(self):
        Assignment = self.env['gt.sales.territory.assignment']
        first = Assignment.create(self.assignment_vals(date_to='2026-01-31'))
        with self.assertRaises((ValidationError, Exception)), self.cr.savepoint():
            Assignment.create(self.assignment_vals(date_from='2026-01-15'))
        with self.assertRaises((ValidationError, Exception)), self.cr.savepoint():
            Assignment.create(self.assignment_vals(date_from='2026-03-01', date_to='2026-02-01'))
        Assignment.create(self.assignment_vals(date_from='2026-02-01'))
        with self.assertRaises((ValidationError, AccessError)):
            Assignment.create(self.assignment_vals(territory=self.territory_b))
        with self.assertRaises((ValidationError, AccessError)):
            Assignment.create(self.assignment_vals(user=self.salesperson_b, date_from='2027-01-01'))
        self.assertTrue(first.exists())

    def test_allocation_uniqueness_reassignment_shared_partner(self):
        Allocation = self.env['gt.sales.territory.customer']
        original = Allocation.create(self.allocation_vals())
        with self.assertRaises(Exception), self.cr.savepoint():
            Allocation.create(self.allocation_vals())
        Allocation.with_company(self.company_b).create(self.allocation_vals(territory=self.territory_b))
        original.write({'active': False})
        Allocation.create(self.allocation_vals())
        self.assertFalse(original.active)
        with self.assertRaises((ValidationError, AccessError)):
            Allocation.create(self.allocation_vals(partner=self.customer_b))

    def test_company_boundaries_and_archiving(self):
        Territory = self.env['gt.sales.territory']
        self.assertFalse(Territory.with_user(self.manager).search([('id', '=', self.territory_b.id)]))
        with self.assertRaises((AccessError, ValidationError)):
            self.territory_b.with_user(self.manager).write({'name': 'Forged'})
        with self.assertRaises((AccessError, ValidationError)):
            Territory.with_user(self.manager).create({'name': 'Forged', 'code': 'F', 'company_id': self.company_b.id})
        assignment = self.env['gt.sales.territory.assignment'].create(self.assignment_vals())
        self.territory_a.write({'active': False})
        self.assertTrue(assignment.exists())
        with self.assertRaises(ValidationError):
            self.env['gt.sales.territory.assignment'].create(self.assignment_vals(date_from='2028-01-01'))

    def test_model_access_independent_of_menu(self):
        for model in ('gt.sales.territory', 'gt.sales.territory.assignment', 'gt.sales.territory.customer'):
            self.assertFalse(self.env[model].with_user(self.unauthorized).has_access('read'))
            self.assertFalse(self.env[model].with_user(self.salesperson_a).has_access('create'))
            self.assertTrue(self.env[model].with_user(self.manager).has_access('create'))

    def test_views_registered(self):
        for name in ('view_territory_form', 'view_territory_list', 'view_assignment_form', 'view_customer_form'):
            self.assertTrue(self.env.ref('gt_field_sales_visit_planner.' + name).arch_db)
