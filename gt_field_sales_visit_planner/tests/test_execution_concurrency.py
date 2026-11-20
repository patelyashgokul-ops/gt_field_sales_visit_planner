"""Execution race tests using independent committed PostgreSQL transactions."""

import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from odoo import Command, api, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestExecutionConcurrency(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = cls.env.registry
        cls.company_id = cls.env.ref('base.main_company').id
        cls.prefix = 'P03-RACE-' + uuid4().hex[:10]
        with cls.registry.cursor() as cr:
            env = api.Environment(cr, cls.env.ref('base.user_admin').id,
                                  {'allowed_company_ids': [cls.company_id]})
            category_user = env.ref('base.group_user')
            salesperson_group = env.ref('gt_field_sales_visit_planner.group_field_salesperson')
            manager_group = env.ref('gt_field_sales_visit_planner.group_sales_manager')
            cls.sales_id = env['res.users'].create({
                'name': cls.prefix, 'login': cls.prefix.lower(),
                'company_id': cls.company_id,
                'company_ids': [Command.set([cls.company_id])],
                'group_ids': [Command.set([category_user.id, salesperson_group.id])],
            }).id
            cls.manager_id = env['res.users'].create({
                'name': cls.prefix + ' manager', 'login': cls.prefix.lower() + '-manager',
                'company_id': cls.company_id,
                'company_ids': [Command.set([cls.company_id])],
                'group_ids': [Command.set([category_user.id, manager_group.id])],
            }).id
            cls.territory_id = env['gt.sales.territory'].create({
                'name': cls.prefix, 'code': cls.prefix, 'company_id': cls.company_id,
            }).id
            env['gt.sales.territory.assignment'].create({
                'territory_id': cls.territory_id, 'company_id': cls.company_id,
                'user_id': cls.sales_id, 'date_from': '2020-01-01',
            })
            cls.partner_ids = env['res.partner'].create([
                {'name': cls.prefix + ' customer 1', 'company_id': cls.company_id},
                {'name': cls.prefix + ' customer 2', 'company_id': cls.company_id},
            ]).ids
            for partner_id in cls.partner_ids:
                env['gt.sales.territory.customer'].create({
                    'territory_id': cls.territory_id, 'company_id': cls.company_id,
                    'partner_id': partner_id,
                })
                env['gt.sales.customer.location'].create({
                    'name': cls.prefix, 'company_id': cls.company_id, 'partner_id': partner_id,
                    'latitude': 12.0, 'longitude': 77.0, 'geofence_radius_m': 100,
                })
            env['gt.sales.execution.policy'].create({
                'company_id': cls.company_id, 'default_radius_m': 100,
                'max_radius_m': 1000, 'max_accuracy_m': 50,
            })
            cls.plan_id = env['gt.sales.visit.plan'].with_user(cls.sales_id).create({
                'company_id': cls.company_id, 'user_id': cls.sales_id,
                'visit_date': fields.Date.today(), 'timezone': 'UTC',
                'territory_id': cls.territory_id,
            }).id
            cls.visit_ids = env['gt.sales.visit'].with_user(cls.sales_id).create([
                {'plan_id': cls.plan_id, 'partner_id': partner_id, 'sequence': (i + 1) * 10}
                for i, partner_id in enumerate(cls.partner_ids)
            ]).ids
            env['gt.sales.visit.plan'].browse(cls.plan_id).with_user(cls.sales_id).action_confirm()
            cr.commit()

    def setUp(self):
        super().setUp()
        # The race fixtures are deliberately committed so worker cursors can see them.
        with self.registry.cursor() as cr:
            cr.execute('DELETE FROM gt_sales_visit_execution_request WHERE visit_id = ANY(%s)', [self.visit_ids])
            cr.execute('DELETE FROM gt_sales_visit_location_exception WHERE visit_id = ANY(%s)', [self.visit_ids])
            cr.execute('DELETE FROM gt_sales_visit_event WHERE visit_id = ANY(%s)', [self.visit_ids])
            cr.execute("UPDATE gt_sales_visit SET state='planned', checkin_at=NULL, checkout_at=NULL, "
                       'checkin_event_id=NULL, checkout_event_id=NULL, checkin_exception_id=NULL, '
                       'checkout_exception_id=NULL, actual_duration_minutes=NULL WHERE id = ANY(%s)', [self.visit_ids])
            cr.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.registry.cursor() as cr:
            cr.execute('DELETE FROM gt_sales_visit_execution_request WHERE visit_id IN (SELECT id FROM gt_sales_visit WHERE plan_id = %s)', [cls.plan_id])
            cr.execute('DELETE FROM gt_sales_visit_location_exception WHERE visit_id IN (SELECT id FROM gt_sales_visit WHERE plan_id = %s)', [cls.plan_id])
            cr.execute('DELETE FROM gt_sales_visit_event WHERE visit_id IN (SELECT id FROM gt_sales_visit WHERE plan_id = %s)', [cls.plan_id])
            cr.execute('DELETE FROM gt_sales_visit WHERE plan_id = %s', [cls.plan_id])
            cr.execute('DELETE FROM gt_sales_visit_plan WHERE id = %s', [cls.plan_id])
            cr.execute('DELETE FROM gt_sales_customer_location WHERE partner_id = ANY(%s)', [cls.partner_ids])
            cr.execute('DELETE FROM gt_sales_execution_policy WHERE company_id = %s', [cls.company_id])
            cr.execute('DELETE FROM gt_sales_territory_customer WHERE territory_id = %s', [cls.territory_id])
            cr.execute('DELETE FROM gt_sales_territory_assignment WHERE territory_id = %s', [cls.territory_id])
            cr.execute('DELETE FROM gt_sales_territory WHERE id = %s', [cls.territory_id])
            cr.execute('DELETE FROM res_partner WHERE id = ANY(%s)', [cls.partner_ids])
            cr.execute('DELETE FROM res_users WHERE id = ANY(%s)', [[cls.sales_id, cls.manager_id]])
            cr.commit()
        super().tearDownClass()

    def _call(self, uid, visit_id, method, payload):
        with self.registry.cursor() as cr:
            env = api.Environment(cr, uid, {'allowed_company_ids': [self.company_id]})
            try:
                result = getattr(env['gt.sales.visit'].browse(visit_id), method)(payload) if payload else getattr(
                    env['gt.sales.visit'].browse(visit_id), method)('Concurrency test')
                cr.commit()
                return ('ok', result)
            except Exception as error:
                cr.rollback()
                return ('rejected', type(error).__name__)

    def _barrier_calls(self, calls):
        ready = threading.Barrier(len(calls))

        def invoke(call):
            ready.wait(timeout=10)
            return self._call(*call)

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = [pool.submit(invoke, call) for call in calls]
            return [future.result(timeout=20) for future in futures]

    @staticmethod
    def _payload(token=None):
        return {'latitude': 12.0, 'longitude': 77.0, 'accuracy_m': 5,
                'request_token': token or str(uuid4())}

    def test_concurrent_duplicate_checkin_is_idempotent(self):
        payload = self._payload()
        outcomes = self._barrier_calls([
            (self.sales_id, self.visit_ids[0], 'action_check_in', payload),
            (self.sales_id, self.visit_ids[0], 'action_check_in', payload),
        ])
        self.assertEqual([status for status, _ in outcomes], ['ok', 'ok'])
        with self.registry.cursor() as cr:
            cr.execute('SELECT count(*) FROM gt_sales_visit_event WHERE visit_id=%s AND event_type=%s', [self.visit_ids[0], 'check_in'])
            self.assertEqual(cr.fetchone()[0], 1)

    def test_concurrent_active_visits_and_checkout(self):
        outcomes = self._barrier_calls([
            (self.sales_id, self.visit_ids[0], 'action_check_in', self._payload()),
            (self.sales_id, self.visit_ids[1], 'action_check_in', self._payload()),
        ])
        self.assertEqual(sorted(status for status, _ in outcomes), ['ok', 'rejected'])
        with self.registry.cursor() as cr:
            cr.execute("SELECT count(*) FROM gt_sales_visit WHERE user_id=%s AND state='checked_in'", [self.sales_id])
            self.assertEqual(cr.fetchone()[0], 1)
        with self.registry.cursor() as cr:
            cr.execute("SELECT id FROM gt_sales_visit WHERE user_id=%s AND state='checked_in' LIMIT 1", [self.sales_id])
            active_id = cr.fetchone()[0]
        checkout = self._payload()
        outcomes = self._barrier_calls([
            (self.sales_id, active_id, 'action_check_out', checkout),
            (self.sales_id, active_id, 'action_check_out', checkout),
        ])
        self.assertEqual([status for status, _ in outcomes], ['ok', 'ok'])
        with self.registry.cursor() as cr:
            cr.execute('SELECT count(*) FROM gt_sales_visit_event WHERE visit_id=%s AND event_type=%s', [active_id, 'check_out'])
            self.assertEqual(cr.fetchone()[0], 1)

    def test_concurrent_cancel_and_checkin_have_one_valid_winner(self):
        visit_id = self.visit_ids[0]
        outcomes = self._barrier_calls([
            (self.sales_id, visit_id, 'action_check_in', self._payload()),
            (self.manager_id, visit_id, 'action_cancel', None),
        ])
        self.assertEqual(sorted(status for status, _ in outcomes), ['ok', 'rejected'])
