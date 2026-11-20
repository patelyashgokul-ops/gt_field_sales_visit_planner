from uuid import uuid4

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
        with self.assertRaises(ValidationError), self.cr.savepoint():
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

    def test_salesperson_reads_only_assigned_territory(self):
        Assignment = self.env['gt.sales.territory.assignment']
        Allocation = self.env['gt.sales.territory.customer']
        Assignment.create(self.assignment_vals())
        mapping = Allocation.create(self.allocation_vals())
        self.assertEqual(self.env['gt.sales.territory'].with_user(self.salesperson_a).search([]).ids, self.territory_a.ids)
        self.assertEqual(Allocation.with_user(self.salesperson_a).search([]).ids, mapping.ids)
        self.assertFalse(self.env['gt.sales.territory'].with_user(self.salesperson_b).search([]))

    def test_application_admin_still_company_scoped(self):
        Territory = self.env['gt.sales.territory']
        self.assertFalse(Territory.with_user(self.app_admin).search([('id', '=', self.territory_b.id)]))
        with self.assertRaises((AccessError, ValidationError)):
            Territory.with_user(self.app_admin).create({
                'name': 'Cross Company', 'code': 'CROSS', 'company_id': self.company_b.id,
            })

    def test_views_registered(self):
        for name in ('view_territory_form', 'view_territory_list', 'view_assignment_form', 'view_customer_form'):
            self.assertTrue(self.env.ref('gt_field_sales_visit_planner.' + name).arch_db)

    def test_portal_user_and_forged_relations(self):
        portal = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Field Portal', 'login': 'field_portal_' + uuid4().hex,
            'group_ids': [(6, 0, [self.env.ref('base.group_portal').id])],
        })
        with self.assertRaises(ValidationError):
            self.env['gt.sales.territory.assignment'].create(
                self.assignment_vals(user=portal, date_from='2029-01-01'))
        with self.assertRaises(ValidationError):
            self.env['gt.sales.territory.customer'].create(
                self.allocation_vals(partner=self.customer_b))
        with self.assertRaises((AccessError, ValidationError)):
            self.env['gt.sales.territory.assignment'].with_user(self.manager).create(
                self.assignment_vals(territory=self.territory_b, user=self.salesperson_b))

    def test_company_change_after_dependents(self):
        assignment = self.env['gt.sales.territory.assignment'].create(self.assignment_vals())
        assignment.write({'active': False})
        with self.assertRaises(ValidationError):
            self.territory_a.write({'company_id': self.company_b.id})
        with self.assertRaises(ValidationError):
            self.territory_a.unlink()

