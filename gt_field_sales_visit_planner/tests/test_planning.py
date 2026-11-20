from odoo.exceptions import AccessError, ValidationError, UserError
from odoo.tests import tagged

from .common import FieldSalesCase


@tagged('post_install', '-at_install')
class TestVisitPlanning(FieldSalesCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['gt.sales.territory.assignment'].create({
            'territory_id': cls.territory_a.id, 'company_id': cls.company_a.id,
            'user_id': cls.salesperson_a.id, 'date_from': '2026-01-01',
        })
        cls.env['gt.sales.territory.customer'].create({
            'territory_id': cls.territory_a.id, 'company_id': cls.company_a.id,
            'partner_id': cls.customer_a.id,
        })

    def plan(self, **extra):
        vals = {'company_id': self.company_a.id, 'user_id': self.salesperson_a.id,
                'visit_date': '2026-05-15', 'timezone': 'Asia/Kolkata',
                'territory_id': self.territory_a.id}
        vals.update(extra)
        return self.env['gt.sales.visit.plan'].with_user(self.manager).with_context(allowed_company_ids=[self.company_a.id]).create(vals)

    def test_plan_and_visit_confirm_and_lock(self):
        plan = self.plan()
        visit = self.env['gt.sales.visit'].with_user(self.manager).with_context(allowed_company_ids=[self.company_a.id]).create({
            'plan_id': plan.id, 'partner_id': self.customer_a.id, 'sequence': 10,
        })
        plan.with_user(self.manager).action_confirm()
        self.assertEqual(plan.state, 'confirmed')
        self.assertEqual(visit.state, 'planned')
        with self.assertRaises(AccessError):
            plan.with_user(self.manager).write({'state': 'draft'})
        with self.assertRaises(UserError):
            visit.with_user(self.manager).write({'planning_notes': 'forged write'})

    def test_duplicate_plan_and_bad_customer(self):
        self.plan()
        with self.assertRaises(ValidationError):
            self.plan()
        plan = self.plan(visit_date='2026-05-16')
        with self.assertRaises(ValidationError):
            self.env['gt.sales.visit'].with_user(self.manager).with_context(allowed_company_ids=[self.company_a.id]).create({
                'plan_id': plan.id, 'partner_id': self.customer_b.id, 'sequence': 10,
            })

    def test_confirm_requires_visits_and_revalidates_mapping(self):
        empty = self.plan(visit_date='2026-05-21')
        with self.assertRaises(ValidationError):
            empty.with_user(self.manager).action_confirm()
        plan = self.plan(visit_date='2026-05-17')
        visit = self.env['gt.sales.visit'].with_user(self.manager).with_context(allowed_company_ids=[self.company_a.id]).create({
            'plan_id': plan.id, 'partner_id': self.customer_a.id, 'sequence': 10,
        })
        self.env['gt.sales.territory.customer'].search([('partner_id', '=', self.customer_a.id)]).write({'active': False})
        with self.assertRaises(ValidationError):
            plan.with_user(self.manager).action_confirm()
        self.assertEqual(plan.state, 'draft')
        self.assertEqual(visit.state, 'draft')

    def test_reschedule_history_and_mandatory_reason(self):
        plan = self.plan(visit_date='2026-05-18')
        visit = self.env['gt.sales.visit'].with_user(self.manager).with_context(allowed_company_ids=[self.company_a.id]).create({
            'plan_id': plan.id, 'partner_id': self.customer_a.id, 'sequence': 10,
        })
        with self.assertRaises(ValidationError):
            visit.with_user(self.manager).action_reschedule('2026-05-19', '')
        replacement = visit.with_user(self.manager).action_reschedule('2026-05-19', 'Customer requested new date')
        self.assertEqual(visit.state, 'rescheduled')
        self.assertEqual(replacement.reschedule_origin_id, visit)
        self.assertEqual(replacement.plan_id.visit_date, visit.plan_id.visit_date.replace(day=19))

    def test_cancel_reason_and_plan_history(self):
        plan = self.plan(visit_date='2026-05-20')
        self.env['gt.sales.visit'].with_user(self.manager).with_context(allowed_company_ids=[self.company_a.id]).create({'plan_id': plan.id, 'partner_id': self.customer_a.id, 'sequence': 10})
        with self.assertRaises(ValidationError):
            plan.with_user(self.manager).action_cancel(' ')
        plan.with_user(self.manager).action_cancel('Weather')
        self.assertEqual(plan.state, 'cancelled')
        with self.assertRaises(AccessError):
            plan.unlink()
