"""Execution race tests using independent committed PostgreSQL transactions."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from odoo import Command, api, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged
from psycopg2.errors import UniqueViolation


@tagged('post_install', '-at_install')
class TestExecutionConcurrency(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.registry = cls.env.registry
        cls.prefix = 'P03-RACE-' + uuid4().hex[:10]
        with cls.registry.cursor() as cr:
            bootstrap = api.Environment(cr, cls.env.ref('base.user_admin').id, {})
            cls.company_id = bootstrap['res.company'].create({'name': cls.prefix}).id
            category_user = bootstrap.ref('base.group_user')
            salesperson_group = bootstrap.ref('gt_field_sales_visit_planner.group_field_salesperson')
            manager_group = bootstrap.ref('gt_field_sales_visit_planner.group_sales_manager')
            cls.sales_id = bootstrap['res.users'].create({
                'name': cls.prefix, 'login': cls.prefix.lower(),
                'company_id': cls.company_id,
                'company_ids': [Command.set([cls.company_id])],
                'group_ids': [Command.set([category_user.id, salesperson_group.id])],
            }).id
            cls.manager_id = bootstrap['res.users'].create({
                'name': cls.prefix + ' manager', 'login': cls.prefix.lower() + '-manager',
                'company_id': cls.company_id,
                'company_ids': [Command.set([cls.company_id])],
                'group_ids': [Command.set([category_user.id, manager_group.id])],
            }).id
            env = api.Environment(cr, cls.manager_id, {'allowed_company_ids': [cls.company_id]})
            cls.territory_id = env['gt.sales.territory'].create({
                'name': cls.prefix, 'code': cls.prefix, 'company_id': cls.company_id,
            }).id
            env['gt.sales.territory.assignment'].create({
                'territory_id': cls.territory_id, 'company_id': cls.company_id,
                'user_id': cls.sales_id, 'date_from': '2020-01-01',
            })
            cls.partner_ids = bootstrap['res.partner'].sudo().create([
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
            cr.execute("UPDATE gt_sales_visit_plan SET state='confirmed', cancellation_reason=NULL, "
                       "cancelled_at=NULL, cancelled_by=NULL WHERE id=%s", [self.plan_id])
            cr.execute('UPDATE gt_sales_visit SET checkin_event_id=NULL, checkout_event_id=NULL, '
                       'checkin_exception_id=NULL, checkout_exception_id=NULL, checkin_at=NULL, checkout_at=NULL, '
                       'actual_duration_minutes=NULL, state=\'planned\' WHERE id = ANY(%s)',
                       [self.visit_ids])
            cr.execute('DELETE FROM gt_sales_visit_execution_request WHERE visit_id = ANY(%s)', [self.visit_ids])
            cr.execute('DELETE FROM gt_sales_visit_location_exception WHERE visit_id = ANY(%s)', [self.visit_ids])
            cr.execute('DELETE FROM mail_activity WHERE gt_source_visit_id = ANY(%s)', [self.visit_ids])
            cr.execute('DELETE FROM gt_sales_visit_event WHERE visit_id = ANY(%s)', [self.visit_ids])
            cr.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.registry.cursor() as cr:
            cr.execute('UPDATE gt_sales_visit SET checkin_event_id=NULL, checkout_event_id=NULL, '
                       'checkin_exception_id=NULL, checkout_exception_id=NULL WHERE plan_id = %s', [cls.plan_id])
            cr.execute('DELETE FROM mail_activity WHERE gt_source_visit_id IN (SELECT id FROM gt_sales_visit WHERE plan_id = %s)', [cls.plan_id])
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
            cr.commit()
        with cls.registry.cursor() as cr:
            env = api.Environment(cr, cls.env.ref('base.user_admin').id, {})
            env['res.users'].sudo().browse([cls.sales_id, cls.manager_id]).unlink()
            env['res.company'].sudo().browse(cls.company_id).unlink()
            cr.commit()
        super().tearDownClass()

    def _call(self, uid, visit_id, operation, payload):
        with self.registry.cursor() as cr:
            cr.execute("SET LOCAL statement_timeout = '15s'")
            try:
                if operation.startswith('event:'):
                    event_type = operation.split(':', 1)[1]
                    cr.execute(
                        'INSERT INTO gt_sales_visit_event '
                        '(visit_id, company_id, actor_user_id, event_type, occurred_at, location_status, policy_snapshot) '
                        'VALUES (%s, %s, %s, %s, NOW(), %s, %s::jsonb) RETURNING id',
                        [visit_id, self.company_id, uid, event_type, 'within_radius', '{}'],
                    )
                    result = cr.fetchone()
                else:
                    target = {'action_check_in': ('checked_in', 'planned'),
                              'action_check_out': ('checked_out_pending_outcome', 'checked_in'),
                              'action_cancel': ('cancelled', 'planned'),
                              'action_reschedule': ('rescheduled', 'planned')}[operation]
                    cr.execute('UPDATE gt_sales_visit SET state=%s WHERE id=%s AND state=%s RETURNING id',
                               [target[0], visit_id, target[1]])
                    result = cr.fetchone()
                    if not result:
                        cr.rollback()
                        return ('rejected', 'stale state')
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
        outcomes = self._barrier_calls([
            (self.sales_id, self.visit_ids[0], 'event:check_in', None),
            (self.sales_id, self.visit_ids[0], 'event:check_in', None),
        ])
        self.assertEqual(sorted(status for status, _ in outcomes), ['ok', 'rejected'])
        with self.registry.cursor() as cr:
            cr.execute('SELECT count(*) FROM gt_sales_visit_event WHERE visit_id=%s AND event_type=%s', [self.visit_ids[0], 'check_in'])
            self.assertEqual(cr.fetchone()[0], 1)

    def test_concurrent_active_visits_and_checkout(self):
        outcomes = self._barrier_calls([
            (self.sales_id, self.visit_ids[0], 'action_check_in', None),
            (self.sales_id, self.visit_ids[1], 'action_check_in', None),
        ])
        self.assertEqual(sorted(status for status, _ in outcomes), ['ok', 'rejected'])
        with self.registry.cursor() as cr:
            cr.execute("SELECT count(*) FROM gt_sales_visit WHERE user_id=%s AND state='checked_in'", [self.sales_id])
            self.assertEqual(cr.fetchone()[0], 1)
        active_id = self.visit_ids[0] if outcomes[0][0] == 'ok' else self.visit_ids[1]
        with self.registry.cursor() as cr:
            cr.execute('INSERT INTO gt_sales_visit_event '
                       '(visit_id,company_id,actor_user_id,event_type,occurred_at,location_status,policy_snapshot) '
                       'VALUES (%s,%s,%s,%s,NOW(),%s,%s::jsonb) RETURNING id',
                       [active_id,self.company_id,self.sales_id,'check_in','within_radius','{}'])
            event_id = cr.fetchone()[0]
            cr.execute("UPDATE gt_sales_visit SET state='checked_in',checkin_at=NOW(),checkin_event_id=%s WHERE id=%s",
                       [event_id,active_id])
            cr.commit()
        outcomes = self._barrier_calls([
            (self.sales_id, active_id, 'event:check_out', None),
            (self.sales_id, active_id, 'event:check_out', None),
        ])
        self.assertEqual(sorted(status for status, _ in outcomes), ['ok', 'rejected'])
        with self.registry.cursor() as cr:
            cr.execute('SELECT count(*) FROM gt_sales_visit_event WHERE visit_id=%s AND event_type=%s', [active_id, 'check_out'])
            self.assertEqual(cr.fetchone()[0], 1)

    def test_concurrent_cancel_and_checkin_have_one_valid_winner(self):
        visit_id = self.visit_ids[0]
        outcomes = self._barrier_calls([
            (self.sales_id, visit_id, 'action_check_in', None),
            (self.manager_id, visit_id, 'action_cancel', None),
        ])
        self.assertEqual(sorted(status for status, _ in outcomes), ['ok', 'rejected'])

    def test_concurrent_reschedule_and_checkin_have_one_valid_winner(self):
        visit_id = self.visit_ids[0]
        outcomes = self._barrier_calls([
            (self.sales_id, visit_id, 'action_check_in', None),
            (self.sales_id, visit_id, 'action_reschedule', None),
        ])
        self.assertEqual(sorted(status for status, _ in outcomes), ['ok', 'rejected'])

    def test_concurrent_followup_source_unique_index(self):
        """Independent transactions cannot attach two obligations to one visit."""
        visit_id = self.visit_ids[0]
        model_id = self.env['ir.model']._get_id('gt.sales.visit')
        todo_id = self.env.ref('mail.mail_activity_data_todo').id
        admin_id = self.env.ref('base.user_admin').id
        ready = threading.Barrier(2)

        def insert_activity():
            ready.wait(timeout=10)
            with self.registry.cursor() as cr:
                try:
                    cr.execute(
                        'INSERT INTO mail_activity '
                        '(res_model_id, res_model, res_id, activity_type_id, user_id, summary, '
                        'date_deadline, gt_source_visit_id, create_uid, write_uid, create_date, write_date) '
                        'VALUES (%s, %s, %s, %s, %s, %s, CURRENT_DATE, %s, %s, %s, NOW(), NOW())',
                        [model_id, 'gt.sales.visit', visit_id, todo_id, self.sales_id,
                         'Concurrent source obligation', visit_id, admin_id, admin_id],
                    )
                    cr.commit()
                    return 'created'
                except UniqueViolation:
                    cr.rollback()
                    return 'rejected'

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(insert_activity) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=20) for f in futures), ['created', 'rejected'])
        with self.registry.cursor() as cr:
            cr.execute('SELECT count(*) FROM mail_activity WHERE gt_source_visit_id=%s', [visit_id])
            self.assertEqual(cr.fetchone()[0], 1)

    def test_concurrent_completion_creates_one_followup(self):
        """The real completion service and source index tolerate simultaneous retries."""
        visit_id = self.visit_ids[0]
        manager_env = self.env['gt.sales.visit.outcome'].with_context(
            allowed_company_ids=[self.company_id]).sudo()
        outcome = manager_env.create({
            'name': 'Concurrent Follow-up ' + uuid4().hex[:8],
            'code': 'CONCURRENT-' + uuid4().hex[:10],
            'company_id': self.company_id,
            'requires_followup': True,
            'followup_allowed': True,
        })
        visit = self.env['gt.sales.visit'].browse(visit_id).with_user(self.sales_id)
        gps = {'latitude': 12.0, 'longitude': 77.0, 'accuracy_m': 5,
               'request_token': str(uuid4())}
        visit.action_check_in(gps)
        visit.action_check_out({**gps, 'request_token': str(uuid4())})
        visit.write({
            'outcome_id': outcome.id,
            'followup_required': True,
            'next_followup_date': fields.Date.today() + timedelta(days=2),
            'next_followup_user_id': self.sales_id,
            'next_followup_summary': 'Concurrent completion follow-up',
        })
        self.env.cr.commit()
        barrier = threading.Barrier(2)

        def complete():
            with self.registry.cursor() as cr:
                env = api.Environment(cr, self.sales_id,
                                      {'allowed_company_ids': [self.company_id]})
                barrier.wait(timeout=10)
                try:
                    result = env['gt.sales.visit'].browse(visit_id).action_complete_visit()
                    cr.commit()
                    return result
                except Exception:
                    cr.rollback()
                    raise

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [f.result(timeout=30) for f in [pool.submit(complete), pool.submit(complete)]]
        self.assertEqual(results[0]['activity_id'], results[1]['activity_id'])
        with self.registry.cursor() as cr:
            cr.execute('SELECT state, completed_at, followup_activity_id FROM gt_sales_visit WHERE id=%s',
                       [visit_id])
            state, completed_at, activity_id = cr.fetchone()
            self.assertEqual(state, 'completed')
            self.assertTrue(completed_at)
            self.assertEqual(activity_id, results[0]['activity_id'])
            cr.execute('SELECT count(*) FROM mail_activity WHERE gt_source_visit_id=%s', [visit_id])
            self.assertEqual(cr.fetchone()[0], 1)
            cr.execute("SELECT count(*) FROM gt_sales_visit_event WHERE visit_id=%s AND event_type='visit_completed'",
                       [visit_id])
            self.assertEqual(cr.fetchone()[0], 1)


