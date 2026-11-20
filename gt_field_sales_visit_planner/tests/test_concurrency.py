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
