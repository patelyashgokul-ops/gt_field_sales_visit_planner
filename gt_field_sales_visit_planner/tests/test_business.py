import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import FieldSalesCase


@tagged('post_install', '-at_install')
class TestVisitBusiness(FieldSalesCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env['gt.sales.territory.assignment'].create({
            'territory_id': cls.territory_a.id, 'company_id': cls.company_a.id,
            'user_id': cls.salesperson_a.id, 'date_from': '2020-01-01',
        })
        cls.env['gt.sales.territory.customer'].create({
            'territory_id': cls.territory_a.id, 'company_id': cls.company_a.id,
            'partner_id': cls.customer_a.id,
        })
        cls.policy = cls.env['gt.sales.execution.policy'].with_user(cls.manager).create({
            'company_id': cls.company_a.id, 'default_radius_m': 100, 'max_radius_m': 1000,
            'max_accuracy_m': 50,
        })
        cls.location = cls.env['gt.sales.customer.location'].with_user(cls.manager).create({
            'name': 'Business test location', 'company_id': cls.company_a.id,
            'partner_id': cls.customer_a.id, 'latitude': 12.0, 'longitude': 77.0,
            'geofence_radius_m': 100,
        })
        cls.outcome = cls.env['gt.sales.visit.outcome'].with_user(cls.manager).create({
            'name': 'P04 Qualified', 'code': 'p04_qualified', 'company_id': cls.company_a.id,
            'requires_notes': True, 'requires_followup': True, 'followup_allowed': True,
            'is_positive': True,
        })
        cls.general = cls.env['gt.sales.visit.outcome'].with_user(cls.manager).create({
            'name': 'P04 General', 'code': 'p04_general', 'company_id': cls.company_a.id,
            'followup_allowed': True,
        })

    def make_visit(self, second=False):
        plan = self.env['gt.sales.visit.plan'].with_user(self.manager).create({
            'company_id': self.company_a.id, 'user_id': self.salesperson_a.id,
            'visit_date': fields.Date.today(), 'timezone': 'UTC', 'territory_id': self.territory_a.id,
        })
        visit = self.env['gt.sales.visit'].with_user(self.manager).create({
            'plan_id': plan.id, 'partner_id': self.customer_a.id, 'sequence': 10,
        })
        plan.with_user(self.manager).action_confirm()
        if second:
            return visit
        gps = {'latitude': 12.0, 'longitude': 77.0, 'accuracy_m': 4, 'request_token': str(uuid.uuid4())}
        visit.with_user(self.salesperson_a).action_check_in(gps)
        visit.with_user(self.salesperson_a).action_check_out({**gps, 'request_token': str(uuid.uuid4())})
        return visit

    def test_completion_creates_one_standard_activity_and_preserves_execution(self):
        visit = self.make_visit()
        checkin, checkout = visit.checkin_event_id, visit.checkout_event_id
        visit.with_user(self.salesperson_a).write({
            'outcome_id': self.outcome.id, 'meeting_summary': 'Discussed product fit and pricing.',
            'followup_required': True, 'next_followup_date': fields.Date.today() + timedelta(days=2),
            'next_followup_user_id': self.salesperson_a.id, 'next_followup_summary': 'Send requested quotation',
        })
        result = visit.with_user(self.salesperson_a).action_complete_visit()
        self.assertEqual(visit.state, 'completed')
        self.assertEqual(visit.followup_activity_id.id, result['activity_id'])
        self.assertEqual(visit.followup_activity_id.res_model, 'gt.sales.visit')
        self.assertEqual(visit.followup_activity_id.res_id, visit.id)
        self.assertEqual(visit.followup_activity_id.gt_source_visit_id, visit)
        self.assertEqual(visit.followup_status, 'scheduled')
        self.assertEqual(visit.checkin_event_id, checkin)
        self.assertEqual(visit.checkout_event_id, checkout)
        completed_at = visit.completed_at
        again = visit.with_user(self.salesperson_a).action_complete_visit()
        self.assertEqual(visit.completed_at, completed_at)
        self.assertEqual(again['activity_id'], result['activity_id'])
        self.assertEqual(self.env['mail.activity'].search_count([('gt_source_visit_id', '=', visit.id)]), 1)
        self.assertEqual(self.env['gt.sales.visit.event'].search_count([
            ('visit_id', '=', visit.id), ('event_type', '=', 'visit_completed')]), 1)

    def test_completion_requires_checkout_outcome_notes_and_followup(self):
        planned = self.make_visit(second=True)
        with self.assertRaises(UserError):
            planned.with_user(self.salesperson_a).action_complete_visit()
        planned.plan_id.with_user(self.manager).action_cancel('Test cleanup before second plan')
        checked_out = self.make_visit()
        with self.assertRaises(ValidationError):
            checked_out.with_user(self.salesperson_a).action_complete_visit()
        with self.assertRaises(ValidationError):
            checked_out.with_user(self.salesperson_a).write({'outcome_id': self.outcome.id})
        with self.assertRaises(ValidationError):
            checked_out.with_user(self.salesperson_a).write({
                'outcome_id': self.outcome.id, 'meeting_summary': 'Discussed next steps.',
                'followup_required': True,
            })
        with self.assertRaises(ValidationError):
            checked_out.with_user(self.salesperson_a).action_complete_visit()
        self.assertEqual(checked_out.state, 'checked_out_pending_outcome')

    def test_outcome_company_archive_and_configuration_security(self):
        with self.assertRaises(AccessError):
            self.env['gt.sales.visit.outcome'].with_user(self.salesperson_a).create({
                'name': 'Forged', 'code': 'forged', 'company_id': self.company_a.id,
            })
        with self.assertRaises(AccessError):
            self.env['gt.sales.visit.outcome'].with_user(self.manager).create({
                'name': 'Foreign', 'code': 'foreign', 'company_id': self.company_b.id,
            })
        archived = self.general
        archived.with_user(self.manager).write({'active': False})
        visit = self.make_visit()
        with self.assertRaises(ValidationError):
            visit.with_user(self.salesperson_a).write({'outcome_id': archived.id})
        with self.assertRaises(AccessError):
            self.outcome.with_user(self.salesperson_a).write({'name': 'Redefined'})

    def test_missed_visit_reason_and_state_protection(self):
        planned = self.make_visit(second=True)
        with self.assertRaises(ValidationError):
            planned.with_user(self.salesperson_a).action_mark_missed(' ')
        planned.with_user(self.salesperson_a).action_mark_missed('Customer premises closed')
        self.assertEqual(planned.state, 'missed')
        self.assertTrue(planned.missed_at and planned.missed_by == self.salesperson_a)
        self.assertEqual(self.env['gt.sales.visit.event'].search_count([
            ('visit_id', '=', planned.id), ('event_type', '=', 'visit_missed')]), 1)
        with self.assertRaises(AccessError):
            planned.with_user(self.salesperson_a).write({'state': 'completed'})
        planned.plan_id.with_user(self.manager).action_cancel('Test cleanup after missed visit')
        checked_out = self.make_visit()
        with self.assertRaises(UserError):
            checked_out.with_user(self.salesperson_a).action_mark_missed('Too late')

    def test_cross_company_crm_product_interest_and_completed_write_denied(self):
        visit = self.make_visit()
        self.salesperson_a.write({'group_ids': [(4, self.env.ref('sales_team.group_sale_salesman').id)]})
        lead = self.env['crm.lead'].with_company(self.company_a).create({
            'name': 'Eligible opportunity', 'partner_id': self.customer_a.id, 'company_id': self.company_a.id,
            'user_id': self.salesperson_a.id,
        })
        foreign = self.env['crm.lead'].with_company(self.company_b).create({
            'name': 'Foreign opportunity', 'partner_id': self.customer_b.id, 'company_id': self.company_b.id,
        })
        with self.assertRaises((AccessError, ValidationError)):
            visit.with_user(self.salesperson_a).write({'crm_lead_id': foreign.id})
        visit.with_user(self.salesperson_a).write({'crm_lead_id': lead.id})
        self.env['gt.sales.visit.product.interest'].with_user(self.salesperson_a).create({
            'visit_id': visit.id, 'product_name': 'Industrial pump', 'interest_level': 'high',
        })
        visit.with_user(self.salesperson_a).write({'outcome_id': self.general.id})
        visit.with_user(self.salesperson_a).action_complete_visit()
        with self.assertRaises(AccessError):
            visit.with_user(self.salesperson_a).write({'meeting_summary': 'Tampered'})
        with self.assertRaises(AccessError):
            visit.with_user(self.salesperson_a).write({'completed_at': fields.Datetime.now()})
        with self.assertRaises(AccessError):
            visit.with_user(self.unauthorized).read(['meeting_summary'])

    def test_manager_correction_and_standard_activity_done_history(self):
        visit = self.make_visit()
        visit.with_user(self.salesperson_a).write({
            'outcome_id': self.outcome.id, 'meeting_summary': 'Initial meeting notes',
            'followup_required': True, 'next_followup_date': fields.Date.today() + timedelta(days=1),
            'next_followup_user_id': self.salesperson_a.id, 'next_followup_summary': 'Call back',
        })
        visit.with_user(self.salesperson_a).action_complete_visit()
        with self.assertRaises(AccessError):
            visit.with_user(self.salesperson_a).action_correct_business_result({'meeting_summary': 'Change'}, 'reason')
        visit.with_user(self.manager).action_correct_business_result({'meeting_summary': 'Corrected notes'}, 'Typo correction')
        self.assertEqual(visit.meeting_summary, 'Corrected notes')
        activity = visit.followup_activity_id
        activity.with_user(self.salesperson_a).action_feedback(feedback='Customer contacted')
        self.assertTrue(visit.followup_completed_at)
        self.assertEqual(visit.followup_status, 'completed')
        self.assertEqual(self.env['gt.sales.visit.event'].search_count([
            ('visit_id', '=', visit.id), ('event_type', '=', 'followup_completed')]), 1)

