import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from psycopg2.errors import ExclusionViolation

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

    def test_duplicate_daily_plan_race(self):
        registry = self.env.registry
        company_id = self.env.ref('base.main_company').id
        user_id = self.env.ref('base.user_admin').id
        code = 'PLAN-RACE-' + uuid4().hex[:10]
        target_date = '2031-03-11'
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
                cr.execute('DELETE FROM gt_sales_visit_plan WHERE company_id=%s AND user_id=%s AND visit_date=%s',
                           [company_id, user_id, target_date])
                cr.execute('DELETE FROM gt_sales_territory WHERE id=%s', [territory_id])
                cr.commit()
