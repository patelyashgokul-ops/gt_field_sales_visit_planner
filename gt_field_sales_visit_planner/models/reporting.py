import csv
import io
import json
from datetime import timedelta

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError


class SalesReporting(models.AbstractModel):
    _name = 'gt.sales.reporting'
    _description = 'Secure Field Sales Reporting Service'

    _REPORTS = {'territories', 'customers', 'salespeople', 'followups', 'exceptions', 'outcomes'}
    _MAX_PERIOD_DAYS = 366
    _EXPORT_LIMIT = 10000
    _FORMULA_PREFIXES = ('=', '+', '-', '@')

    @api.model
    def _is_manager(self):
        return self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager')

    @api.model
    def _authorize(self):
        if not (self.env.user.has_group('gt_field_sales_visit_planner.group_field_salesperson')
                or self._is_manager()):
            raise AccessError('You are not authorized to access Field Sales reports.')
        self.env['gt.sales.visit'].check_access('read')

    @api.model
    def _validated_filters(self, filters=None):
        self._authorize()
        filters = filters or {}
        if not isinstance(filters, dict) or set(filters) - {'company_id', 'date_from', 'date_to', 'territory_id', 'user_id'}:
            raise ValidationError('The report filters are invalid.')
        allowed_company_ids = self.env.companies.ids
        company_id = int(filters.get('company_id') or self.env.company.id)
        if company_id not in allowed_company_ids:
            raise AccessError('You cannot report on that company.')
        company = self.env['res.company'].browse(company_id)
        today = fields.Date.context_today(self.with_context(tz=self.env.user.tz or 'UTC'))
        date_from = fields.Date.to_date(filters.get('date_from')) if filters.get('date_from') else today.replace(day=1)
        date_to = fields.Date.to_date(filters.get('date_to')) if filters.get('date_to') else today
        if date_to < date_from:
            raise ValidationError('End date must be on or after start date.')
        if (date_to - date_from).days + 1 > self._MAX_PERIOD_DAYS:
            raise ValidationError('Choose a reporting period of 366 days or less.')
        timezone = company.partner_id.tz or self.env.user.tz or 'UTC'
        if timezone not in pytz.all_timezones_set:
            timezone = 'UTC'
        manager = self._is_manager()
        user_id = int(filters.get('user_id') or 0) or False
        if not manager:
            if user_id and user_id != self.env.uid:
                raise AccessError('Salespersons can only report on their own visits.')
            user_id = self.env.uid
        elif user_id:
            user = self.env['res.users'].browse(user_id)
            if (not user.exists() or company not in user.company_ids or user.share
                    or not user.has_group('gt_field_sales_visit_planner.group_field_salesperson')):
                raise AccessError('Choose an eligible salesperson in the selected company.')
        territory_id = int(filters.get('territory_id') or 0) or False
        if territory_id:
            territory = self.env['gt.sales.territory'].search([
                ('id', '=', territory_id), ('company_id', '=', company_id), ('active', '=', True),
            ], limit=1)
            if not territory:
                raise AccessError('You cannot report on that territory.')
        return {
            'company_id': company_id, 'date_from': date_from, 'date_to': date_to,
            'territory_id': territory_id, 'user_id': user_id, 'timezone': timezone,
            'manager': manager, 'today': fields.Date.context_today(self.with_context(tz=timezone)),
        }

    @api.model
    def _scope_domain(self, f):
        domain = [
            ('company_id', '=', f['company_id']),
            ('planned_date', '>=', f['date_from']), ('planned_date', '<=', f['date_to']),
        ]
        if f['territory_id']:
            domain.append(('territory_id', '=', f['territory_id']))
        if f['user_id']:
            domain.append(('user_id', '=', f['user_id']))
        return domain

    @api.model
    def _completed_domain(self, f):
        return self._scope_domain(f) + [
            ('state', '=', 'completed'), ('completed_at', '!=', False),
            ('checkin_event_id', '!=', False), ('checkout_event_id', '!=', False),
        ]

    @api.model
    def _coverage_rows(self, f, limit=20):
        # Flush ORM writes in this transaction before the parameterized aggregate.
        self.env['gt.sales.territory'].flush_model(['company_id', 'active', 'name'])
        self.env['gt.sales.territory.customer'].flush_model(['territory_id', 'company_id', 'partner_id', 'active'])
        self.env['gt.sales.visit'].flush_model(['company_id', 'territory_id', 'partner_id', 'user_id', 'planned_date',
                                                'state', 'completed_at', 'checkin_event_id', 'checkout_event_id'])
        # SQL is used only for this distinct allocation/visit join. The company is
        # checked against env.companies above; salesperson access is independently
        # constrained to their own visits and active assigned territory dates.
        clauses = ["t.company_id = %s", "t.active IS TRUE", "m.company_id = t.company_id", "m.active IS TRUE",
                   "v.company_id = t.company_id", "v.territory_id = t.id", "v.partner_id = m.partner_id",
                   "v.state = 'completed'", "v.completed_at IS NOT NULL", "v.checkin_event_id IS NOT NULL",
                   "v.checkout_event_id IS NOT NULL", "v.planned_date >= %s", "v.planned_date <= %s"]
        params = [f['company_id'], f['date_from'], f['date_to']]
        if f['territory_id']:
            clauses.append('t.id = %s')
            params.append(f['territory_id'])
        if f['user_id']:
            clauses.append('v.user_id = %s')
            params.append(f['user_id'])
        if not f['manager']:
            clauses.append("EXISTS (SELECT 1 FROM gt_sales_territory_assignment a WHERE a.territory_id=t.id "
                           "AND a.company_id=t.company_id AND a.user_id=%s AND a.active IS TRUE "
                           "AND a.date_from <= %s AND (a.date_to IS NULL OR a.date_to >= %s))")
            params.extend([self.env.uid, f['date_to'], f['date_from']])
        sql = f"""
            SELECT t.id, COUNT(DISTINCT m.partner_id), COUNT(DISTINCT v.partner_id), COUNT(v.id),
                   MAX(v.completed_at)
              FROM gt_sales_territory t
              JOIN gt_sales_territory_customer m ON m.territory_id=t.id AND m.company_id=t.company_id AND m.active IS TRUE
              LEFT JOIN gt_sales_visit v ON {' AND '.join(clauses)}
             WHERE t.company_id=%s AND t.active IS TRUE
             GROUP BY t.id ORDER BY t.name, t.id LIMIT %s
        """
        # The company placeholder is also in the LEFT JOIN to prevent a visit from
        # another company from joining. `params` order follows that join first.
        params.extend([f['company_id'], limit])
        self.env.cr.execute(sql, params)
        grouped = self.env.cr.fetchall()
        territories = self.env['gt.sales.territory'].browse([row[0] for row in grouped])
        names = {t.id: t.display_name for t in territories}
        result = []
        for territory_id, allocated, visited, completed, last_visit in grouped:
            not_visited = max(allocated - visited, 0)
            result.append({
                'territory_id': territory_id, 'territory': names.get(territory_id, ''),
                'allocated_customers': allocated, 'customers_visited': visited,
                'customers_not_visited': not_visited,
                'coverage_percent': round(visited * 100.0 / allocated, 1) if allocated else None,
                'completed_visits': completed, 'repeat_visits': max(completed - visited, 0),
                'last_completed_at': fields.Datetime.to_string(last_visit) if last_visit else '',
                'coverage_basis': 'Current Allocation Coverage',
            })
        return result

    @api.model
    def _outcome_rows(self, f, limit=20):
        self.env['gt.sales.visit'].flush_model(['company_id', 'planned_date', 'state', 'completed_at',
                                                'checkin_event_id', 'checkout_event_id', 'outcome_snapshot',
                                                'territory_id', 'user_id'])
        # Outcome name/code come from the immutable completion-time snapshot,
        # not mutable outcome master data.
        clauses = ["company_id=%s", "planned_date >= %s", "planned_date <= %s",
                   "state='completed'", "completed_at IS NOT NULL", "checkin_event_id IS NOT NULL",
                   "checkout_event_id IS NOT NULL", "outcome_snapshot IS NOT NULL"]
        params = [f['company_id'], f['date_from'], f['date_to']]
        if f['territory_id']:
            clauses.append('territory_id=%s'); params.append(f['territory_id'])
        if f['user_id']:
            clauses.append('user_id=%s'); params.append(f['user_id'])
        self.env.cr.execute(f"""
            SELECT outcome_snapshot->>'code', outcome_snapshot->>'name', COUNT(*)
              FROM gt_sales_visit WHERE {' AND '.join(clauses)}
             GROUP BY outcome_snapshot->>'code', outcome_snapshot->>'name'
             ORDER BY COUNT(*) DESC, outcome_snapshot->>'name' LIMIT %s
        """, params + [limit])
        return [{'code': code or '', 'outcome': name or 'Unspecified', 'visits': count}
                for code, name, count in self.env.cr.fetchall()]

    @api.model
    def _status_counts(self, f):
        visits = self.env['gt.sales.visit']
        scope = self._scope_domain(f)
        confirmed = [('plan_id.state', '=', 'confirmed')]
        completed = visits.search_count(self._completed_domain(f))
        eligible = visits.search_count(scope + [('plan_id.confirmed_at', '!=', False), ('state', 'in', [
            'planned', 'checked_in', 'checked_out_pending_outcome', 'completed', 'missed'])])
        states = {
            'planned': visits.search_count(scope + confirmed + [('state', '=', 'planned')]),
            'checked_in': visits.search_count(scope + [('state', '=', 'checked_in')]),
            'pending_outcome': visits.search_count(scope + [('state', '=', 'checked_out_pending_outcome')]),
            'completed': completed,
            'missed': visits.search_count(scope + [('state', '=', 'missed')]),
            'cancelled': visits.search_count(scope + [('state', '=', 'cancelled')]),
            'rescheduled': visits.search_count(scope + [('state', '=', 'rescheduled')]),
        }
        unexecuted_overdue = visits.search_count(scope + confirmed + [
            ('state', '=', 'planned'), ('planned_date', '<', f['today'])])
        return {'eligible_planned': eligible, 'completed': completed, 'states': states,
                'unexecuted': states['planned'], 'overdue_unexecuted': unexecuted_overdue,
                'completion_rate': round(completed * 100.0 / eligible, 1) if eligible else None}

    @api.model
    def _followup_counts(self, f):
        Activity = self.env['mail.activity']
        domain = [
            ('gt_source_visit_id', '!=', False),
            ('gt_source_visit_id.company_id', '=', f['company_id']),
            ('gt_source_visit_id.planned_date', '>=', f['date_from']),
            ('gt_source_visit_id.planned_date', '<=', f['date_to']),
            ('gt_source_visit_id.followup_completed_at', '=', False),
        ]
        if f['territory_id']:
            domain.append(('gt_source_visit_id.territory_id', '=', f['territory_id']))
        if f['user_id']:
            domain.append(('gt_source_visit_id.user_id', '=', f['user_id']))
        domain.append(('active', '=', True))
        open_count = Activity.search_count(domain)
        overdue = Activity.search_count(domain + [('date_deadline', '<', f['today'])])
        due = Activity.search_count(domain + [('date_deadline', '=', f['today'])])
        completed = self.env['gt.sales.visit'].search_count(self._scope_domain(f) + [
            ('followup_required', '=', True), ('followup_completed_at', '!=', False)])
        return {'open': open_count, 'overdue': overdue, 'due_today': due, 'completed': completed,
                'basis': 'Follow-ups linked to visits scheduled in the selected period; overdue is as of company reporting date.'}

    @api.model
    def _exception_counts(self, f):
        domain = [
            ('company_id', '=', f['company_id']), ('visit_id.planned_date', '>=', f['date_from']),
            ('visit_id.planned_date', '<=', f['date_to']),
        ]
        if f['territory_id']:
            domain.append(('visit_id.territory_id', '=', f['territory_id']))
        if f['user_id']:
            domain.append(('user_id', '=', f['user_id']))
        Exception = self.env['gt.sales.visit.location.exception']
        return {state: Exception.search_count(domain + [('review_state', '=', state)])
                for state in ('pending', 'approved', 'rejected')}

    @api.model
    def _trend_rows(self, f):
        grouped = self.env['gt.sales.visit']._read_group(
            self._scope_domain(f) + [('plan_id.confirmed_at', '!=', False)],
            ['planned_date:day', 'state'], ['__count'], order='planned_date:day', limit=400,
        )
        points = {}
        for day, state, count in grouped:
            key = fields.Date.to_string(day)
            point = points.setdefault(key, {'date': key, 'planned': 0, 'completed': 0, 'missed': 0})
            if state in ('planned', 'checked_in', 'checked_out_pending_outcome', 'completed', 'missed'):
                point['planned'] += count
            if state == 'completed':
                point['completed'] += count
            elif state == 'missed':
                point['missed'] += count
        return list(points.values())

    @api.model
    def _salesperson_rows(self, f, limit=20):
        groups = self.env['gt.sales.visit']._read_group(
            self._scope_domain(f) + [('plan_id.confirmed_at', '!=', False)],
            ['user_id', 'territory_id', 'state'], ['__count'], order='user_id, territory_id', limit=limit * 8,
        )
        values = {}
        for user, territory, state, count in groups:
            key = (user.id, territory.id)
            row = values.setdefault(key, {'user_id': user.id, 'salesperson': user.display_name,
                                           'territory_id': territory.id, 'territory': territory.display_name,
                                           'planned': 0, 'completed': 0, 'missed': 0, 'unexecuted': 0,
                                           'checked_in': 0, 'pending_outcome': 0})
            if state in ('planned', 'checked_in', 'checked_out_pending_outcome', 'completed', 'missed'):
                row['planned'] += count
            if state == 'completed': row['completed'] += count
            if state == 'missed': row['missed'] += count
            if state == 'planned': row['unexecuted'] += count
            if state == 'checked_in': row['checked_in'] += count
            if state == 'checked_out_pending_outcome': row['pending_outcome'] += count
        result = list(values.values())
        for row in result:
            row['completion_rate'] = round(row['completed'] * 100.0 / row['planned'], 1) if row['planned'] else None
        return sorted(result, key=lambda row: (-row['completed'], row['salesperson']))[:limit]

    @api.model
    def _customer_rows(self, f, limit=20):
        Allocation = self.env['gt.sales.territory.customer']
        map_domain = [('company_id', '=', f['company_id']), ('active', '=', True)]
        if f['territory_id']:
            map_domain.append(('territory_id', '=', f['territory_id']))
        mappings = Allocation.search(map_domain, order='id', limit=self._EXPORT_LIMIT + 1)
        if len(mappings) > self._EXPORT_LIMIT:
            raise ValidationError('Customer report exceeds 10,000 active allocations; add a territory filter.')
        partner_ids = mappings.mapped('partner_id').ids
        if not partner_ids:
            return []
        domain = self._completed_domain(f) + [('partner_id', 'in', partner_ids)]
        grouped = self.env['gt.sales.visit']._read_group(
            domain, ['partner_id', 'territory_id'], ['__count', 'completed_at:min', 'completed_at:max'],
            order='__count desc, partner_id', limit=limit,
        )
        return [{'partner_id': partner.id, 'customer': partner.display_name,
                 'territory_id': territory.id, 'territory': territory.display_name,
                 'completed_visits': count, 'last_completed_at': fields.Datetime.to_string(last) if last else '',
                 'first_completed_at': fields.Datetime.to_string(first) if first else ''}
                for partner, territory, count, first, last in grouped]

    @api.model
    def _followup_rows(self, f, limit=100):
        domain = [
            ('gt_source_visit_id', '!=', False), ('active', '=', True),
            ('gt_source_visit_id.company_id', '=', f['company_id']),
            ('date_deadline', '>=', f['date_from']), ('date_deadline', '<=', f['date_to']),
            ('gt_source_visit_id.followup_completed_at', '=', False),
        ]
        if f['territory_id']:
            domain.append(('gt_source_visit_id.territory_id', '=', f['territory_id']))
        if f['user_id']:
            domain.append(('gt_source_visit_id.user_id', '=', f['user_id']))
        activities = self.env['mail.activity'].search(domain, order='date_deadline, id', limit=limit)
        result = []
        for activity in activities:
            visit = activity.gt_source_visit_id
            result.append({'visit_id': visit.id, 'customer': visit.partner_id.display_name,
                           'salesperson': visit.user_id.display_name, 'followup_owner': activity.user_id.display_name,
                           'summary': activity.summary, 'due_date': fields.Date.to_string(activity.date_deadline),
                           'status': 'overdue' if activity.date_deadline < f['today'] else
                                     'due' if activity.date_deadline == f['today'] else 'open',
                           'days_overdue': max((f['today'] - activity.date_deadline).days, 0),
                           'crm_lead_id': visit.crm_lead_id.id if visit.crm_lead_id else False})
        return result

    @api.model
    def _exception_rows(self, f, limit=100):
        domain = [
            ('company_id', '=', f['company_id']), ('visit_id.planned_date', '>=', f['date_from']),
            ('visit_id.planned_date', '<=', f['date_to']),
        ]
        if f['territory_id']:
            domain.append(('visit_id.territory_id', '=', f['territory_id']))
        if f['user_id']:
            domain.append(('user_id', '=', f['user_id']))
        exceptions = self.env['gt.sales.visit.location.exception'].search(domain, order='create_date desc, id desc', limit=limit)
        return [{'visit_id': ex.visit_id.id, 'customer': ex.visit_id.partner_id.display_name,
                 'salesperson': ex.user_id.display_name, 'territory': ex.visit_id.territory_id.display_name,
                 'event': dict(ex._fields['event_type'].selection).get(ex.event_type, ex.event_type),
                 'classification': dict(ex._fields['location_status'].selection).get(ex.location_status, ex.location_status),
                 'review_state': ex.review_state, 'reason': dict(ex._fields['reason_code'].selection).get(ex.reason_code, ex.reason_code),
                 'reviewed_at': fields.Datetime.to_string(ex.reviewed_at) if ex.reviewed_at else ''}
                for ex in exceptions]

    @api.model
    def _visit_rows(self, f, limit=100):
        visits = self.env['gt.sales.visit'].search(self._scope_domain(f), order='planned_date desc, user_id, sequence, id', limit=limit)
        return [{'visit_id': visit.id, 'customer': visit.partner_id.display_name,
                 'salesperson': visit.user_id.display_name, 'territory': visit.territory_id.display_name,
                 'planned_date': fields.Date.to_string(visit.planned_date),
                 'status': dict(visit._fields['state'].selection).get(visit.state, visit.state),
                 'outcome': (visit.outcome_snapshot or {}).get('name', ''),
                 'followup_status': visit.followup_status,
                 'location_status': dict(visit._fields['checkin_location_status'].selection).get(visit.checkin_location_status, '')}
                for visit in visits]

    @api.model
    def _report_data(self, key, f, limit=100):
        if key == 'territories': return self._coverage_rows(f, limit)
        if key == 'customers': return self._customer_rows(f, limit)
        if key == 'salespeople': return self._salesperson_rows(f, limit)
        if key == 'followups': return self._followup_rows(f, limit)
        if key == 'exceptions': return self._exception_rows(f, limit)
        if key == 'outcomes': return self._outcome_rows(f, limit)
        if key == 'visits': return self._visit_rows(f, limit)
        raise ValidationError('Unknown Field Sales report.')

    @api.model
    def get_dashboard_data(self, filters=None):
        f = self._validated_filters(filters)
        counts = self._status_counts(f)
        followups = self._followup_counts(f)
        exceptions = self._exception_counts(f)
        coverage_all = self._coverage_rows(f, limit=10000)
        coverage = coverage_all[:20]
        outcomes = self._outcome_rows(f, limit=10)
        salespeople = self._salesperson_rows(f, limit=10)
        territories = self.env['gt.sales.territory'].search([('company_id', '=', f['company_id']), ('active', '=', True)])
        if not f['manager']:
            salespeople_options = [{'id': self.env.uid, 'name': self.env.user.display_name}]
        else:
            assignments = self.env['gt.sales.territory.assignment'].search([
                ('company_id', '=', f['company_id']), ('active', '=', True),
            ])
            users = assignments.mapped('user_id')
            salespeople_options = [{'id': user.id, 'name': user.display_name} for user in users.sorted('name')]
        company = self.env['res.company'].browse(f['company_id'])
        return {
            'filters': {'company_id': f['company_id'], 'date_from': fields.Date.to_string(f['date_from']),
                        'date_to': fields.Date.to_string(f['date_to']), 'territory_id': f['territory_id'],
                        'user_id': f['user_id'], 'timezone': f['timezone']},
            'companies': [{'id': c.id, 'name': c.display_name} for c in self.env.companies],
            'territories': [{'id': t.id, 'name': t.display_name} for t in territories],
            'salespeople': salespeople_options,
            'kpis': {**counts, 'coverage_percent': round(
                sum(row['customers_visited'] for row in coverage_all) * 100.0 /
                sum(row['allocated_customers'] for row in coverage_all), 1)
                if sum(row['allocated_customers'] for row in coverage_all) else None,
                'territories_covered': sum(1 for row in coverage_all if row['customers_visited']),
                'territory_count': len(coverage_all), 'overdue_followups': followups['overdue'],
                'pending_gps_exceptions': exceptions['pending']},
            'states': counts['states'], 'followups': followups, 'exceptions': exceptions,
            'coverage': coverage, 'outcomes': outcomes, 'salespeople_rows': salespeople,
            'trends': self._trend_rows(f), 'current_allocation_label': 'Current Allocation Coverage',
            'date_basis': 'Visits use scheduled local visit date; follow-up due dates use company reporting date.',
        }

    @api.model
    def get_report_rows(self, report, filters=None, limit=100):
        f = self._validated_filters(filters)
        if report not in self._REPORTS | {'visits'}:
            raise ValidationError('Unknown Field Sales report.')
        if not isinstance(limit, int) or limit < 1 or limit > self._EXPORT_LIMIT:
            raise ValidationError('Report row limit must be between 1 and 10,000.')
        return self._report_data(report, f, limit)

    @api.model
    def get_export_payload(self, report, filters=None):
        f = self._validated_filters(filters)
        if report not in self._REPORTS | {'visits'}:
            raise ValidationError('Unknown Field Sales export.')
        rows = self._report_data(report, f, self._EXPORT_LIMIT)
        if report == 'territories':
            columns = [('territory', 'Territory'), ('allocated_customers', 'Allocated Customers'),
                       ('customers_visited', 'Customers Visited'), ('customers_not_visited', 'Customers Not Visited'),
                       ('coverage_percent', 'Coverage %'), ('completed_visits', 'Completed Visits'),
                       ('repeat_visits', 'Repeat Visits'), ('last_completed_at', 'Last Completed Visit'),
                       ('coverage_basis', 'Coverage Basis')]
        elif report == 'customers':
            columns = [('customer', 'Customer'), ('territory', 'Territory'), ('completed_visits', 'Completed Visits'),
                       ('first_completed_at', 'First Completed Visit'), ('last_completed_at', 'Latest Completed Visit')]
        elif report == 'salespeople':
            columns = [('salesperson', 'Salesperson'), ('territory', 'Territory'), ('planned', 'Eligible Planned'),
                       ('completed', 'Completed'), ('missed', 'Missed'), ('unexecuted', 'Unexecuted'),
                       ('completion_rate', 'Completion %')]
        elif report == 'followups':
            columns = [('customer', 'Customer'), ('salesperson', 'Visit Owner'), ('followup_owner', 'Follow-up Owner'),
                       ('summary', 'Follow-up'), ('due_date', 'Due Date'), ('status', 'Status'), ('days_overdue', 'Days Overdue')]
        elif report == 'exceptions':
            columns = [('customer', 'Customer'), ('salesperson', 'Salesperson'), ('territory', 'Territory'),
                       ('event', 'Event'), ('classification', 'Location Classification'), ('review_state', 'Review Status'),
                       ('reason', 'Reason'), ('reviewed_at', 'Reviewed At')]
        elif report == 'outcomes':
            columns = [('code', 'Outcome Code'), ('outcome', 'Outcome'), ('visits', 'Completed Visits')]
        else:
            columns = [('customer', 'Customer'), ('salesperson', 'Salesperson'), ('territory', 'Territory'),
                       ('planned_date', 'Scheduled Local Date'), ('status', 'Visit Status'), ('outcome', 'Business Outcome'),
                       ('followup_status', 'Follow-up Status'), ('location_status', 'Check-in Location Assessment')]
        safe_rows = [[row.get(key, '') for key, _label in columns] for row in rows]
        filename = f'field_sales_{report}_{f["date_from"]}_{f["date_to"]}'
        return {'columns': [label for _key, label in columns], 'rows': safe_rows, 'filename': filename,
                'company': self.env['res.company'].browse(f['company_id']).display_name,
                'date_from': fields.Date.to_string(f['date_from']), 'date_to': fields.Date.to_string(f['date_to'])}

    @api.model
    def get_drilldown_action(self, metric, filters=None):
        f = self._validated_filters(filters)
        domain = self._scope_domain(f)
        if metric in ('eligible_planned', 'completion_rate', 'coverage'):
            domain += [('plan_id.confirmed_at', '!=', False), ('state', 'in', [
                'planned', 'checked_in', 'checked_out_pending_outcome', 'completed', 'missed'])]
        elif metric == 'completed':
            domain = self._completed_domain(f)
        elif metric == 'missed':
            domain.append(('state', '=', 'missed'))
        elif metric == 'unexecuted':
            domain += [('plan_id.state', '=', 'confirmed'), ('state', '=', 'planned')]
        elif metric == 'pending_outcome':
            domain.append(('state', '=', 'checked_out_pending_outcome'))
        else:
            raise ValidationError('This dashboard metric has no visit drill-down.')
        return {'type': 'ir.actions.act_window', 'name': 'Field Sales Visits', 'res_model': 'gt.sales.visit',
                'view_mode': 'list,form', 'domain': domain, 'context': {'search_default_group_by_user_id': 0}}

    @api.model
    def spreadsheet_safe_text(self, value):
        text = '' if value is None else str(value)
        probe = text.lstrip(' \t\r\n\x00\x0b\x0c\ufeff')
        if probe.startswith(self._FORMULA_PREFIXES):
            return "'" + text
        return text
