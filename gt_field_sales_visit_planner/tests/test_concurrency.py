import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from uuid import uuid4

from psycopg2.errors import ExclusionViolation
from odoo import api

from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestConcurrentAssignment(TransactionCase):
    def test_overlapping_creates_in_separate_transactions(self):
        registry = self.env.registry
        company_id = self.env.ref('base.main_company').id
        user_id = self.env.ref('base.user_admin').id
        code = 'RACE-' + uuid4().hex[:12]
        with registry.cursor() as cr:
            cr.execute(
                'INSERT INTO gt_sales_territory (name, code, company_id, active) '
                'VALUES (%s, %s, %s, true) RETURNING id',
                [code, code, company_id],
            )
            territory_id = cr.fetchone()[0]
            cr.commit()

        created = threading.Event()
        attempting = threading.Event()
        release = threading.Event()

        def worker(first):
            with registry.cursor() as cr:
                cr.execute("SET LOCAL lock_timeout = '10s'")
                if not first:
                    attempting.set()
                try:
                    cr.execute(
                        'INSERT INTO gt_sales_territory_assignment '
                        '(territory_id, company_id, user_id, date_from, active) '
                        'VALUES (%s, %s, %s, %s, true)',
                        [territory_id, company_id, user_id, '2026-01-01'],
                    )
                    if first:
                        created.set()
                        if not release.wait(10):
                            raise TimeoutError('Concurrent test release timed out')
                    cr.commit()
                    return 'created'
                except ExclusionViolation:
                    cr.rollback()
                    return 'rejected'

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(worker, True)
                self.assertTrue(created.wait(10))
                second = pool.submit(worker, False)
                self.assertTrue(attempting.wait(10))
                time.sleep(0.2)
                release.set()
                self.assertEqual(sorted([first.result(15), second.result(15)]), ['created', 'rejected'])
        finally:
            release.set()
            with registry.cursor() as cr:
                cr.execute('DELETE FROM gt_sales_territory_assignment WHERE territory_id = %s', [territory_id])
                cr.execute('DELETE FROM gt_sales_territory WHERE id = %s', [territory_id])
                cr.commit()

    def test_duplicate_customer_allocation_race(self):
        """The partial unique index must arbitrate concurrent active mappings."""
        registry = self.env.registry
        company_id = self.env.ref('base.main_company').id
        code = 'MAP-RACE-' + uuid4().hex[:12]
        with registry.cursor() as cr:
            partner_env = api.Environment(
                cr, self.env.ref('base.user_admin').id,
                {'allowed_company_ids': [company_id]},
            )
            partner = partner_env['res.partner'].sudo().create({'name': code})
            partner_id = partner.id
            cr.commit()
        with registry.cursor() as cr:
            cr.execute(
                'INSERT INTO gt_sales_territory (name, code, company_id, active) '
                'VALUES (%s, %s, %s, true) RETURNING id',
                [code, code, company_id],
            )
            territory_id = cr.fetchone()[0]
            cr.commit()

        inserted = threading.Event()
        attempting = threading.Event()
        release = threading.Event()

        def worker(first):
            with registry.cursor() as cr:
                cr.execute("SET LOCAL lock_timeout = '10s'")
                if not first:
                    attempting.set()
                try:
                    cr.execute(
                        'INSERT INTO gt_sales_territory_customer '
                        '(territory_id, company_id, partner_id, active) '
                        'VALUES (%s, %s, %s, true)',
                        [territory_id, company_id, partner_id],
                    )
                    if first:
                        inserted.set()
                        if not release.wait(10):
                            raise TimeoutError('Concurrent mapping test timed out')
                    cr.commit()
                    return 'created'
                except Exception as error:
                    cr.rollback()
                    if getattr(getattr(error, 'diag', None), 'constraint_name', '') == \
                            'gt_sales_territory_customer_one_active_customer':
                        return 'rejected'
                    raise

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(worker, True)
                self.assertTrue(inserted.wait(10))
                second = pool.submit(worker, False)
                self.assertTrue(attempting.wait(10))
                time.sleep(0.2)
                release.set()
                self.assertEqual(sorted([first.result(15), second.result(15)]),
                                 ['created', 'rejected'])
            with registry.cursor() as cr:
                cr.execute(
                    'SELECT count(*) FROM gt_sales_territory_customer '
                    'WHERE company_id = %s AND partner_id = %s AND active IS TRUE',
                    [company_id, partner_id],
                )
                self.assertEqual(cr.fetchone()[0], 1)
        finally:
            release.set()
            with registry.cursor() as cr:
                cr.execute('DELETE FROM gt_sales_territory_customer WHERE territory_id = %s',
                           [territory_id])
                cr.execute('DELETE FROM gt_sales_territory WHERE id = %s', [territory_id])
                cr.execute('DELETE FROM res_partner WHERE id = %s', [partner_id])
                cr.commit()

    def test_duplicate_daily_plan_race(self):
        registry = self.env.registry
        company_id = self.env.ref('base.main_company').id
        user_id = self.env.ref('base.user_admin').id
        code = 'PLAN-RACE-' + uuid4().hex[:10]
        target_date = (date(2040, 1, 1) + timedelta(days=int(uuid4().hex[:6], 16) % 100000)).isoformat()
        with registry.cursor() as cr:
            cr.execute('INSERT INTO gt_sales_territory (name,code,company_id,active) VALUES (%s,%s,%s,true) RETURNING id',
                       [code, code, company_id])
            territory_id = cr.fetchone()[0]
            cr.commit()
        go = threading.Event()

        def worker():
            with registry.cursor() as cr:
                cr.execute("SET LOCAL lock_timeout = '10s'")
                go.wait(10)
                try:
                    cr.execute("INSERT INTO gt_sales_visit_plan (name,company_id,user_id,visit_date,timezone,territory_id,state,active) VALUES (%s,%s,%s,%s,'UTC',%s,'draft',true)",
                               [code, company_id, user_id, target_date, territory_id])
                    cr.commit()
                    return 'created'
                except Exception as error:
                    cr.rollback()
                    if getattr(getattr(error, 'diag', None), 'constraint_name', '') == 'gt_sales_visit_plan_unique_live_day':
                        return 'rejected'
                    raise
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                one, two = pool.submit(worker), pool.submit(worker)
                go.set()
                self.assertEqual(sorted([one.result(15), two.result(15)]), ['created', 'rejected'])
        finally:
            with registry.cursor() as cr:
                cr.execute('DELETE FROM gt_sales_visit WHERE plan_id IN (SELECT id FROM gt_sales_visit_plan WHERE company_id=%s AND user_id=%s AND visit_date=%s)',
                           [company_id, user_id, target_date])
                cr.execute('DELETE FROM gt_sales_visit_plan WHERE company_id=%s AND user_id=%s AND visit_date=%s',
                           [company_id, user_id, target_date])
                cr.execute('DELETE FROM gt_sales_territory WHERE id=%s', [territory_id])
                cr.commit()

    def test_concurrent_reschedule_of_same_visit_creates_one_replacement(self):
        registry = self.env.registry
        company_id = self.env.ref('base.main_company').id
        user_id = self.env.ref('base.user_admin').id
        code = 'RESCHEDULE-RACE-' + uuid4().hex[:10]
        start_date = date(2040, 1, 1) + timedelta(days=int(uuid4().hex[:6], 16) % 100000)
        source_date, target_date_one, target_date_two = [
            (start_date + timedelta(days=offset)).isoformat() for offset in range(3)
        ]
        with registry.cursor() as cr:
            env = api.Environment(cr, user_id, {'allowed_company_ids': [company_id]})
            territory = env['gt.sales.territory'].sudo().create({
                'name': code, 'code': code, 'company_id': company_id,
            })
            assignment = env['gt.sales.territory.assignment'].sudo().create({
                'territory_id': territory.id, 'company_id': company_id,
                'user_id': user_id, 'date_from': '2031-01-01',
            })
            partner = env['res.partner'].sudo().create({'name': code})
            mapping = env['gt.sales.territory.customer'].sudo().create({
                'territory_id': territory.id, 'company_id': company_id,
                'partner_id': partner.id,
            })
            plan = env['gt.sales.visit.plan'].sudo().create({
                'company_id': company_id, 'user_id': user_id, 'visit_date': source_date,
                'timezone': 'UTC', 'territory_id': territory.id,
            })
            visit = env['gt.sales.visit'].sudo().create({
                'plan_id': plan.id, 'partner_id': partner.id, 'sequence': 10,
            })
            target_one = env['gt.sales.visit.plan'].sudo().create({
                'company_id': company_id, 'user_id': user_id, 'visit_date': target_date_one,
                'timezone': 'UTC', 'territory_id': territory.id,
            })
            target_two = env['gt.sales.visit.plan'].sudo().create({
                'company_id': company_id, 'user_id': user_id, 'visit_date': target_date_two,
                'timezone': 'UTC', 'territory_id': territory.id,
            })
            territory_id, partner_id, visit_id = territory.id, partner.id, visit.id
            target_one_id, target_two_id = target_one.id, target_two.id
            cr.commit()

        first_created = threading.Event()
        second_attempting = threading.Event()
        release_first = threading.Event()

        def worker(first):
            with registry.cursor() as cr:
                cr.execute("SET LOCAL lock_timeout = '10s'")
                if not first:
                    second_attempting.set()
                try:
                    target_plan_id = target_one_id if first else target_two_id
                    cr.execute(
                        'INSERT INTO gt_sales_visit '
                        '(name, plan_id, company_id, territory_id, user_id, partner_id, sequence, '
                        'planned_date, state, reschedule_origin_id, reschedule_reason, create_uid, '
                        'create_date, write_uid, write_date) '
                        'SELECT %s, p.id, p.company_id, p.territory_id, p.user_id, %s, 10, '
                        'p.visit_date, %s, %s, %s, %s, NOW(), %s, NOW() '
                        'FROM gt_sales_visit_plan p WHERE p.id = %s RETURNING id',
                        [code, partner_id, 'draft', visit_id, 'Concurrency test reschedule',
                         user_id, user_id, target_plan_id],
                    )
                    cr.fetchone()
                    if first:
                        first_created.set()
                        if not release_first.wait(10):
                            raise TimeoutError('Concurrent reschedule release timed out')
                    cr.commit()
                    return 'created'
                except Exception as error:
                    cr.rollback()
                    if getattr(getattr(error, 'diag', None), 'constraint_name', '') == 'gt_sales_visit_unique_replacement_origin':
                        return 'rejected'
                    return ('unexpected', traceback.format_exc())

        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(worker, True)
                self.assertTrue(first_created.wait(10))
                second = pool.submit(worker, False)
                self.assertTrue(second_attempting.wait(10))
                time.sleep(0.2)
                release_first.set()
                self.assertEqual(sorted([first.result(15), second.result(15)]), ['created', 'rejected'])
            with registry.cursor() as cr:
                cr.execute('SELECT count(*) FROM gt_sales_visit WHERE reschedule_origin_id = %s', [visit_id])
                self.assertEqual(cr.fetchone()[0], 1)
        finally:
            release_first.set()
            with registry.cursor() as cr:
                cr.execute('DELETE FROM gt_sales_visit WHERE plan_id IN (SELECT id FROM gt_sales_visit_plan WHERE territory_id = %s)', [territory_id])
                cr.execute('DELETE FROM gt_sales_visit_plan WHERE territory_id = %s', [territory_id])
                cr.execute('DELETE FROM gt_sales_territory_customer WHERE territory_id = %s', [territory_id])
                cr.execute('DELETE FROM gt_sales_territory_assignment WHERE territory_id = %s', [territory_id])
                cr.execute('DELETE FROM gt_sales_territory WHERE id = %s', [territory_id])
                cr.execute('DELETE FROM res_partner WHERE id = %s', [partner_id])
                cr.commit()
