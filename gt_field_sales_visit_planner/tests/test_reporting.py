import uuid
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import FieldSalesCase


@tagged('post_install', '-at_install')
class TestSalesReporting(FieldSalesCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.salesperson_a2 = cls._user('field_sales_a2_report', cls.company_a, 'group_field_salesperson')
        for user in (cls.salesperson_a, cls.salesperson_a2):
            cls.env['gt.sales.territory.assignment'].create({
                'territory_id': cls.territory_a.id, 'company_id': cls.company_a.id,
                'user_id': user.id, 'date_from': '2020-01-01',
            })
        cls.env['gt.sales.territory.customer'].create([
            {'territory_id': cls.territory_a.id, 'company_id': cls.company_a.id, 'partner_id': partner.id}
            for partner in (cls.shared_customer, cls.customer_a)
        ])
        cls.unvisited = cls.env['res.partner'].create({'name': 'Never Visited A', 'company_id': cls.company_a.id})
        cls.env['gt.sales.territory.customer'].create({
            'territory_id': cls.territory_a.id, 'company_id': cls.company_a.id, 'partner_id': cls.unvisited.id,
        })
        cls.env['gt.sales.execution.policy'].with_user(cls.manager).create({
            'company_id': cls.company_a.id, 'default_radius_m': 150, 'max_radius_m': 1000, 'max_accuracy_m': 50,
        })
        cls.env['gt.sales.customer.location'].with_user(cls.manager).create({
            'name': 'Reporting customer location', 'company_id': cls.company_a.id,
            'partner_id': cls.shared_customer.id, 'latitude': 12.0, 'longitude': 77.0,
            'geofence_radius_m': 150,
        })
        cls.outcome = cls.env['gt.sales.visit.outcome'].with_user(cls.manager).create({
            'name': 'Reporting General', 'code': 'report_general', 'company_id': cls.company_a.id,
            'followup_allowed': True,
        })

    def _new_plan(self, user, partners):
        Plan = self.env['gt.sales.visit.plan'].with_user(user)
        plan = Plan.create({'company_id': self.company_a.id, 'user_id': user.id,
                            'visit_date': fields.Date.today(), 'timezone': 'UTC', 'territory_id': self.territory_a.id})
        visits = self.env['gt.sales.visit'].with_user(user).create([
            {'plan_id': plan.id, 'partner_id': partner.id, 'sequence': (index + 1) * 10}
            for index, partner in enumerate(partners)
        ])
        plan.action_confirm()
        return plan, visits

    def _complete(self, visit, user):
        payload = {'latitude': 12.0, 'longitude': 77.0, 'accuracy_m': 5, 'request_token': str(uuid.uuid4())}
        visit.with_user(user).action_check_in(payload)
        visit.with_user(user).action_check_out({**payload, 'request_token': str(uuid.uuid4())})
        visit.with_user(user).write({'outcome_id': self.outcome.id, 'meeting_summary': 'Reporting integration visit'})
        visit.with_user(user).action_complete_visit()

    def test_counts_and_coverage_use_distinct_completed_customer_cohort(self):
        _plan_a, visits_a = self._new_plan(self.salesperson_a, [self.shared_customer, self.customer_a, self.unvisited])
        self._complete(visits_a[0], self.salesperson_a)
        visits_a[1].with_user(self.salesperson_a).action_mark_missed('Customer unavailable')
        _plan_b, visits_b = self._new_plan(self.salesperson_a2, [self.shared_customer])
        self._complete(visits_b[0], self.salesperson_a2)

        filters = {'company_id': self.company_a.id, 'date_from': fields.Date.today(),
                   'date_to': fields.Date.today(), 'territory_id': self.territory_a.id}
        result = self.env['gt.sales.reporting'].with_user(self.manager).get_dashboard_data(filters)
        self.assertEqual(result['kpis']['eligible_planned'], 4)
        self.assertEqual(result['kpis']['completed'], 2)
        self.assertEqual(result['kpis']['states']['missed'], 1)
        self.assertEqual(result['kpis']['unexecuted'], 1)
        self.assertEqual(result['kpis']['completion_rate'], 50.0)
        coverage = next(row for row in result['coverage'] if row['territory_id'] == self.territory_a.id)
        self.assertEqual(coverage['allocated_customers'], 3)
        self.assertEqual(coverage['customers_visited'], 1)
        self.assertEqual(coverage['customers_not_visited'], 2)
        self.assertEqual(coverage['repeat_visits'], 1)
        self.assertEqual(coverage['coverage_basis'], 'Current Allocation Coverage')

    def test_zero_denominator_and_invalid_ranges(self):
        service = self.env['gt.sales.reporting'].with_user(self.manager)
        empty = service.get_dashboard_data({'company_id': self.company_a.id,
            'date_from': '2020-01-01', 'date_to': '2020-01-31'})
        self.assertEqual(empty['kpis']['eligible_planned'], 0)
        self.assertIsNone(empty['kpis']['completion_rate'])
        with self.assertRaises(ValidationError):
            service.get_dashboard_data({'company_id': self.company_a.id,
                'date_from': '2026-03-02', 'date_to': '2026-03-01'})
        with self.assertRaises(ValidationError):
            service.get_dashboard_data({'company_id': self.company_a.id,
                'date_from': '2025-01-01', 'date_to': '2026-06-01'})

    def test_salesperson_report_scope_and_forged_company_filter(self):
        _plan, visits = self._new_plan(self.salesperson_a, [self.shared_customer])
        result = self.env['gt.sales.reporting'].with_user(self.salesperson_a).get_dashboard_data({
            'company_id': self.company_a.id, 'date_from': fields.Date.today(), 'date_to': fields.Date.today(),
        })
        self.assertEqual(result['kpis']['eligible_planned'], 1)
        self.assertEqual(result['salespeople'][0]['id'], self.salesperson_a.id)
        with self.assertRaises(AccessError):
            self.env['gt.sales.reporting'].with_user(self.salesperson_a).get_dashboard_data({
                'company_id': self.company_b.id, 'date_from': fields.Date.today(), 'date_to': fields.Date.today(),
            })
        with self.assertRaises(AccessError):
            self.env['gt.sales.reporting'].with_user(self.salesperson_a).get_dashboard_data({
                'company_id': self.company_a.id, 'user_id': self.salesperson_a2.id,
                'date_from': fields.Date.today(), 'date_to': fields.Date.today(),
            })
        with self.assertRaises(AccessError):
            self.env['gt.sales.reporting'].with_user(self.unauthorized).get_dashboard_data({})

    def test_snapshot_outcome_and_report_drilldown_consistency(self):
        _plan, visits = self._new_plan(self.salesperson_a, [self.shared_customer])
        self._complete(visits[0], self.salesperson_a)
        self.outcome.with_user(self.manager).write({'name': 'Renamed outcome later'})
        service = self.env['gt.sales.reporting'].with_user(self.manager)
        filters = {'company_id': self.company_a.id, 'date_from': fields.Date.today(), 'date_to': fields.Date.today()}
        rows = service.get_report_rows('outcomes', filters)
        self.assertEqual(rows[0]['outcome'], 'Reporting General')
        action = service.get_drilldown_action('completed', filters)
        drilldown_count = self.env['gt.sales.visit'].with_user(self.manager).search_count(action['domain'])
        self.assertEqual(drilldown_count, 1)

    def test_spreadsheet_formula_neutralization(self):
        service = self.env['gt.sales.reporting']
        for value in ('=1+1', '+cmd', '-1+1', '@SUM(A1:A2)', '\t=HYPERLINK("https://bad")'):
            self.assertTrue(service.spreadsheet_safe_text(value).startswith("'"))
        self.assertEqual(service.spreadsheet_safe_text('Normal, "quoted"\ntext'), 'Normal, "quoted"\ntext')
