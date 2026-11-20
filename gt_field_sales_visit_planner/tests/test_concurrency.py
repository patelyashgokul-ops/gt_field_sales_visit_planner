import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from psycopg2.errors import ExclusionViolation

from odoo import api
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestConcurrentAssignment(TransactionCase):
    def test_overlapping_creates_in_separate_transactions(self):
        registry = self.env.registry
        company_id = self.env.ref('base.main_company').id
        user_id = self.env.ref('base.user_admin').id
        code = 'RACE-' + uuid4().hex[:12]
        with registry.cursor() as cr:
            env = api.Environment(cr, 1, {})
            territory = env['gt.sales.territory'].create({
                'name': code, 'code': code, 'company_id': company_id,
            })
            territory_id = territory.id
            cr.commit()

        created = threading.Event()
        attempting = threading.Event()
        release = threading.Event()

        def worker(first):
            with registry.cursor() as cr:
                env = api.Environment(cr, 1, {})
                cr.execute("SET LOCAL lock_timeout = '10s'")
                if not first:
                    attempting.set()
                try:
                    env['gt.sales.territory.assignment'].create({
                        'territory_id': territory_id, 'company_id': company_id,
                        'user_id': user_id, 'date_from': '2026-01-01',
                    })
                    if first:
                        created.set()
                        if not release.wait(10):
                            raise TimeoutError('Concurrent test release timed out')
                    cr.commit()
                    return 'created'
                except (ValidationError, ExclusionViolation):
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
