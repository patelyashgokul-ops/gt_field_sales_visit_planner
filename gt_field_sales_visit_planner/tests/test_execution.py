import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged

from .common import FieldSalesCase


@tagged('post_install', '-at_install')
class TestVisitExecution(FieldSalesCase):
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
        cls.policy = cls.env['gt.sales.execution.policy'].with_user(cls.manager).create({
            'company_id': cls.company_a.id, 'default_radius_m': 100, 'max_radius_m': 1000,
            'max_accuracy_m': 50, 'missing_policy': 'review', 'outside_policy': 'review',
            'low_accuracy_policy': 'review', 'uncertain_policy': 'review',
            'not_configured_policy': 'block',
        })
        cls.location = cls.env['gt.sales.customer.location'].with_user(cls.manager).create({
            'name': 'Customer A entrance', 'company_id': cls.company_a.id,
            'partner_id': cls.customer_a.id, 'latitude': 12.9716,
            'longitude': 77.5946, 'geofence_radius_m': 100,
        })

    def create_planned_visit(self, **plan_values):
        date = plan_values.pop('visit_date', fields.Date.today())
        plan = self.env['gt.sales.visit.plan'].with_user(self.manager).with_context(
            allowed_company_ids=[self.company_a.id]).create({
                'company_id': self.company_a.id, 'user_id': self.salesperson_a.id,
                'visit_date': date, 'timezone': 'UTC', 'territory_id': self.territory_a.id,
                **plan_values,
            })
        visit = self.env['gt.sales.visit'].with_user(self.manager).with_context(
            allowed_company_ids=[self.company_a.id]).create({
                'plan_id': plan.id, 'partner_id': self.customer_a.id, 'sequence': 10,
            })
        plan.with_user(self.manager).action_confirm()
        return visit

    def payload(self, token=None, **values):
        return {'latitude': 12.9716, 'longitude': 77.5946, 'accuracy_m': 5,
                'request_token': token or str(uuid.uuid4()), **values}

    def execute_as_salesperson(self, visit, method, payload):
        return getattr(visit.with_user(self.salesperson_a).with_context(
            allowed_company_ids=[self.company_a.id]), method)(payload)

    def test_successful_checkin_checkout_and_authoritative_duration(self):
        visit = self.create_planned_visit()
        result = self.execute_as_salesperson(visit, 'action_check_in', self.payload())
        self.assertEqual(result['location_status'], 'within_radius')
        self.assertEqual(visit.state, 'checked_in')
        checkin = visit.checkin_at
        self.assertTrue(checkin)
        checkout = self.execute_as_salesperson(visit, 'action_check_out', self.payload())
        self.assertEqual(checkout['state'], 'checked_out_pending_outcome')
        self.assertEqual(visit.state, 'checked_out_pending_outcome')
        self.assertGreaterEqual(visit.actual_duration_minutes, 0)
        self.assertGreaterEqual(visit.checkout_at, checkin)
        self.assertEqual(visit.execution_event_ids.mapped('event_type'), ['check_in', 'check_out'])

    def test_checkout_requires_checkin_and_duplicate_transitions_are_rejected(self):
        visit = self.create_planned_visit()
        with self.assertRaises(UserError):
            self.execute_as_salesperson(visit, 'action_check_out', self.payload())
        payload = self.payload()
        first = self.execute_as_salesperson(visit, 'action_check_in', payload)
        replay = self.execute_as_salesperson(visit, 'action_check_in', payload)
        self.assertEqual(first['event_id'], replay['event_id'])
        with self.assertRaises(ValidationError):
            self.execute_as_salesperson(visit, 'action_check_in', self.payload(
                token=payload['request_token'], latitude=12.9720))
        checkout_payload = self.payload()
        self.execute_as_salesperson(visit, 'action_check_out', checkout_payload)
        with self.assertRaises(UserError):
            self.execute_as_salesperson(visit, 'action_check_out', self.payload())

    def test_forged_execution_state_fields_and_audit_mutation_are_denied(self):
        visit = self.create_planned_visit()
        with self.assertRaises(AccessError):
            visit.with_user(self.manager).write({'checkin_at': fields.Datetime.now()})
        with self.assertRaises(AccessError):
            self.env['gt.sales.visit'].with_user(self.manager).create({
                'plan_id': visit.plan_id.id, 'partner_id': self.customer_a.id,
                'sequence': 20, 'state': 'checked_in', 'checkin_at': fields.Datetime.now(),
            })
        self.execute_as_salesperson(visit, 'action_check_in', self.payload())
        event = visit.checkin_event_id
        with self.assertRaises(AccessError):
            event.with_user(self.manager).write({'latitude': 0})
        with self.assertRaises(AccessError):
            event.with_user(self.manager).unlink()

    def test_gps_validation_accuracy_and_conservative_boundary(self):
        visit = self.create_planned_visit()
        for invalid in ({'latitude': 91}, {'latitude': float('nan')}, {'longitude': float('inf')}):
            with self.assertRaises(ValidationError):
                self.execute_as_salesperson(visit, 'action_check_in', self.payload(**invalid))
        low = self.payload(accuracy_m=60)
        result = self.execute_as_salesperson(visit, 'action_check_in', low)
        self.assertEqual(result['location_status'], 'low_accuracy')
        self.assertEqual(visit.checkin_exception_id.review_state, 'pending')
        self.assertFalse(visit.checkin_event_id.reference_location_id.id is False)
        self.assertEqual(visit.checkin_event_id.reference_radius_m, 100)

    def test_missing_location_exception_approval_rejection_and_self_review(self):
        self.policy.with_user(self.manager).write({'missing_policy': 'review'})
        visit = self.create_planned_visit()
        result = self.execute_as_salesperson(visit, 'action_check_in', {
            'request_token': str(uuid.uuid4()), 'explanation': 'Browser permission was denied',
        })
        self.assertEqual(result['location_status'], 'missing')
        exception = visit.checkin_exception_id
        with self.assertRaises(AccessError):
            exception.with_user(self.salesperson_a).action_review('approved', 'self approval')
        exception.with_user(self.manager).action_review('approved', 'Reviewed the submitted explanation')
        self.assertEqual(exception.review_state, 'approved')
        self.assertEqual(visit.checkin_event_id.location_status, 'missing')
        with self.assertRaises(UserError):
            exception.with_user(self.manager).action_review('rejected', 'duplicate decision')

    def test_block_policy_rolls_back_without_state_or_event(self):
        self.policy.with_user(self.manager).write({'outside_policy': 'block'})
        visit = self.create_planned_visit()
        with self.assertRaises(UserError):
            self.execute_as_salesperson(visit, 'action_check_in', self.payload(latitude=13.02))
        self.assertEqual(visit.state, 'planned')
        self.assertFalse(visit.execution_event_ids)

    def test_missing_reference_location_never_classifies_within_radius(self):
        self.location.with_user(self.manager).write({'active': False})
        self.policy.with_user(self.manager).write({'not_configured_policy': 'review'})
        visit = self.create_planned_visit()
        result = self.execute_as_salesperson(visit, 'action_check_in', self.payload())
        self.assertEqual(result['location_status'], 'not_configured')
        self.assertEqual(visit.checkin_exception_id.review_state, 'pending')
        self.assertFalse(visit.checkin_event_id.reference_location_id)

    def test_local_visit_date_and_confirmed_plan_are_required(self):
        yesterday = fields.Date.subtract(fields.Date.today(), days=1)
        visit = self.create_planned_visit(visit_date=yesterday)
        with self.assertRaises(UserError):
            self.execute_as_salesperson(visit, 'action_check_in', self.payload())
        self.assertEqual(visit.state, 'planned')

    def test_unconfirmed_plan_does_not_allow_execution(self):
        plan = self.env['gt.sales.visit.plan'].with_user(self.manager).create({
            'company_id': self.company_a.id, 'user_id': self.salesperson_a.id,
            'visit_date': fields.Date.today(), 'timezone': 'UTC', 'territory_id': self.territory_a.id,
        })
        visit = self.env['gt.sales.visit'].with_user(self.manager).create({
            'plan_id': plan.id, 'partner_id': self.customer_a.id, 'sequence': 10,
        })
        with self.assertRaises(UserError):
            self.execute_as_salesperson(visit, 'action_check_in', self.payload())
        self.assertEqual(visit.state, 'draft')

    def test_cancelled_and_rescheduled_visits_cannot_check_in(self):
        cancelled = self.create_planned_visit()
        cancelled.with_user(self.salesperson_a).action_cancel('Customer unavailable')
        with self.assertRaises(UserError):
            self.execute_as_salesperson(cancelled, 'action_check_in', self.payload())

        rescheduled = self.create_planned_visit(visit_date=fields.Date.add(fields.Date.today(), days=1))
        replacement = rescheduled.with_user(self.salesperson_a).action_reschedule(
            fields.Date.add(fields.Date.today(), days=2), 'Customer requested a new date')
        with self.assertRaises(UserError):
            self.execute_as_salesperson(rescheduled, 'action_check_in', self.payload())
        self.assertEqual(rescheduled.state, 'rescheduled')
        self.assertEqual(replacement.reschedule_origin_id, rescheduled)

    def test_simultaneous_active_visit_and_missing_policy_block(self):
        plan = self.env['gt.sales.visit.plan'].with_user(self.manager).create({
            'company_id': self.company_a.id, 'user_id': self.salesperson_a.id,
            'visit_date': fields.Date.today(), 'timezone': 'UTC', 'territory_id': self.territory_a.id,
        })
        visit = self.env['gt.sales.visit'].with_user(self.manager).create({
            'plan_id': plan.id, 'partner_id': self.customer_a.id, 'sequence': 10,
        })
        self.env['gt.sales.territory.customer'].create({
            'territory_id': self.territory_a.id, 'company_id': self.company_a.id,
            'partner_id': self.shared_customer.id,
        })
        second = self.env['gt.sales.visit'].with_user(self.manager).create({
            'plan_id': plan.id, 'partner_id': self.shared_customer.id, 'sequence': 20,
        })
        plan.action_confirm()
        self.execute_as_salesperson(visit, 'action_check_in', self.payload())
        with self.assertRaises(UserError):
            self.execute_as_salesperson(second, 'action_check_in', self.payload())
        self.assertEqual(second.state, 'planned')
        self.assertFalse(second.execution_event_ids)

        # Absence of a company policy is deliberately fail-closed.
        self.policy.with_user(self.manager).unlink()
        fresh = self.create_planned_visit(visit_date=fields.Date.add(fields.Date.today(), days=1))
        with self.assertRaises(UserError):
            self.execute_as_salesperson(fresh, 'action_check_in', self.payload())
        self.assertEqual(fresh.state, 'planned')

    def test_manager_rejection_duplicate_review_and_no_coordinate_leak(self):
        visit = self.create_planned_visit()
        self.execute_as_salesperson(visit, 'action_check_in', {
            'request_token': str(uuid.uuid4()), 'explanation': 'Position unavailable',
        })
        exception = visit.checkin_exception_id
        exception.with_user(self.manager).action_review('rejected', 'Please retry at the customer site')
        self.assertEqual(exception.review_state, 'rejected')
        self.assertEqual(visit.checkin_event_id.location_status, 'missing')
        with self.assertRaises(UserError):
            exception.with_user(self.manager).action_review('approved', 'Conflicting second decision')

    def test_reference_location_permissions_cross_company_event_and_import_bypass(self):
        with self.assertRaises(AccessError):
            self.location.with_user(self.salesperson_a).write({'latitude': 12.0})
        visit = self.create_planned_visit()
        with self.assertRaises((AccessError, ValidationError)):
            self.env['gt.sales.visit.event'].sudo().create({
                'visit_id': visit.id, 'company_id': self.company_b.id,
                'actor_user_id': self.manager.id, 'event_type': 'check_in',
                'occurred_at': fields.Datetime.now(), 'location_status': 'within_radius',
                'policy_snapshot': {},
            })
        result = self.env['gt.sales.visit'].with_user(self.manager).load(
            ['id', 'checkin_at'], [[str(visit.id), fields.Datetime.to_string(fields.Datetime.now())]])
        self.assertTrue(result['messages'])
        self.assertNotEqual(visit.state, 'checked_in')

    def test_negative_duration_and_event_read_is_owner_scoped(self):
        visit = self.create_planned_visit()
        self.execute_as_salesperson(visit, 'action_check_in', self.payload())
        visit.sudo()._apply_transition({'checkin_at': fields.Datetime.now() + timedelta(minutes=1)})
        with self.assertRaises(ValidationError):
            self.execute_as_salesperson(visit, 'action_check_out', self.payload())
        self.assertEqual(visit.state, 'checked_in')
        self.assertFalse(visit.checkout_event_id)

        other = self.env['res.users'].create({
            'name': 'Other field salesperson', 'login': 'other_field_sales_p03',
            'company_id': self.company_a.id, 'company_ids': [(6, 0, [self.company_a.id])],
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id,
                                  self.env.ref('gt_field_sales_visit_planner.group_field_salesperson').id])],
        })
        with self.assertRaises(AccessError):
            visit.execution_event_ids.with_user(other).read(['latitude', 'longitude'])

    def test_company_location_boundaries_and_policy_snapshot(self):
        visit = self.create_planned_visit()
        self.execute_as_salesperson(visit, 'action_check_in', self.payload())
        snapshot = dict(visit.checkin_policy_snapshot)
        self.policy.with_user(self.manager).write({'default_radius_m': 250, 'max_accuracy_m': 25})
        self.location.with_user(self.manager).write({'latitude': 13.0, 'longitude': 77.0})
        self.assertEqual(visit.checkin_policy_snapshot, snapshot)
        with self.assertRaises((AccessError, ValidationError)):
            self.env['gt.sales.customer.location'].with_user(self.manager).create({
                'name': 'Wrong company', 'company_id': self.company_a.id,
                'partner_id': self.customer_b.id, 'latitude': 1, 'longitude': 1,
                'geofence_radius_m': 10,
            })
