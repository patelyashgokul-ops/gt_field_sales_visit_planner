import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .common import check_allowed_company


class SalesVisitPlan(models.Model):
    _name = 'gt.sales.visit.plan'
    _description = 'Daily Field Visit Plan'
    _order = 'visit_date desc, user_id, id desc'
    _check_company_auto = True

    name = fields.Char(required=True, default='New', copy=False, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True, default=lambda s: s.env.company)
    user_id = fields.Many2one('res.users', required=True, index=True, check_company=True,
                              default=lambda s: s.env.user)
    visit_date = fields.Date(required=True, index=True, default=fields.Date.context_today)
    timezone = fields.Char(required=True, default=lambda s: s.env.user.tz or 'UTC')
    territory_id = fields.Many2one('gt.sales.territory', required=True, index=True, check_company=True)
    state = fields.Selection([('draft', 'Draft'), ('confirmed', 'Confirmed'), ('cancelled', 'Cancelled')],
                             required=True, default='draft', index=True, copy=False)
    visit_ids = fields.One2many('gt.sales.visit', 'plan_id', copy=True)
    planned_visit_count = fields.Integer(compute='_compute_planned_visit_count', store=True)
    active = fields.Boolean(default=True, index=True)
    confirmed_at = fields.Datetime(readonly=True, copy=False)
    confirmed_by = fields.Many2one('res.users', readonly=True, copy=False)
    cancellation_reason = fields.Text(copy=False)
    cancelled_at = fields.Datetime(readonly=True, copy=False)
    cancelled_by = fields.Many2one('res.users', readonly=True, copy=False)
    notes = fields.Text()

    _unique_live_day = models.UniqueIndex(
        '(company_id, user_id, visit_date) WHERE state != \'cancelled\' AND active IS TRUE',
        'Only one active plan per salesperson, company, and date is allowed.',
    )

    @api.depends('visit_ids.state')
    def _compute_planned_visit_count(self):
        for plan in self:
            plan.planned_visit_count = len(plan.visit_ids.filtered(lambda v: v.state in ('draft', 'planned')))

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        for vals in vals_list:
            if not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
                vals['user_id'] = self.env.user.id
            company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
            if company not in self.env.companies:
                raise AccessError('You cannot create plans for this company.')
            vals['company_id'] = company.id
            user = self.env['res.users'].browse(vals.get('user_id') or self.env.user.id)
            territory = self.env['gt.sales.territory'].browse(vals.get('territory_id'))
            if not user.exists() or user.share or not user.has_group('base.group_user'):
                raise ValidationError('Choose an internal salesperson.')
            if company not in user.company_ids:
                raise ValidationError('The salesperson must belong to the plan company.')
            if not territory.exists() or territory.company_id != company or not territory.active:
                raise ValidationError('Choose an active territory in the plan company.')
            if not vals.get('timezone'):
                vals['timezone'] = user.tz or 'UTC'
            self._validate_timezone(vals['timezone'])
            self._check_user_territory(user, territory, vals.get('visit_date') or fields.Date.context_today(self))
        try:
            with self.env.cr.savepoint():
                plans = super().create(vals_list)
                for plan in plans.filtered(lambda p: p.name == 'New'):
                    plan.name = self.env['ir.sequence'].next_by_code('gt.sales.visit.plan') or '/'
                return plans
        except Exception as error:
            if self._is_unique_plan_error(error):
                raise ValidationError('This salesperson already has a plan for that date.') from error
            raise

    @staticmethod
    def _is_unique_plan_error(error):
        return getattr(getattr(error, 'diag', None), 'constraint_name', '') == 'gt_sales_visit_plan_unique_live_day'

    @staticmethod
    def _validate_timezone(timezone):
        if timezone not in pytz.all_timezones_set:
            raise ValidationError('Choose a valid IANA timezone.')

    def _check_user_territory(self, user, territory, date):
        assignment = self.env['gt.sales.territory.assignment'].with_context(active_test=False).search([
            ('territory_id', '=', territory.id), ('user_id', '=', user.id), ('company_id', '=', territory.company_id.id),
            ('active', '=', True), ('date_from', '<=', date), '|', ('date_to', '=', False), ('date_to', '>=', date),
        ], limit=1)
        if not assignment:
            raise ValidationError('The salesperson is not assigned to this territory on the plan date.')

    def write(self, vals):
        protected = {'company_id', 'user_id', 'visit_date', 'territory_id', 'timezone'}
        for plan in self:
            check_allowed_company(plan)
            if plan.state != 'draft' and (protected & vals.keys() or 'state' in vals):
                raise UserError('Confirmed or cancelled plans require an authorized planning action.')
            if 'user_id' in vals and not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
                raise AccessError('Salespersons can only plan their own visits.')
            if vals.get('state'):
                raise AccessError('Use the confirm or cancellation action to change plan state.')
        result = super().write(vals)
        for plan in self:
            plan._validate_plan_context()
        return result

    def _validate_plan_context(self):
        check_allowed_company(self)
        self._validate_timezone(self.timezone)
        if self.company_id not in self.user_id.company_ids or self.territory_id.company_id != self.company_id:
            raise ValidationError('Plan company, salesperson, and territory must match.')
        self._check_user_territory(self.user_id, self.territory_id, self.visit_date)

    def action_confirm(self):
        self.check_access('write')
        for plan in self:
            check_allowed_company(plan)
            if plan.state != 'draft':
                raise UserError('Only draft plans can be confirmed.')
            if plan.user_id != self.env.user and not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
                raise AccessError('You can only confirm your own plan.')
            plan._validate_plan_context()
            visits = plan.visit_ids.filtered(lambda v: v.state == 'draft')
            if not visits:
                raise ValidationError('Add at least one valid visit before confirming the plan.')
            plan._validate_sequences(visits)
            visits._validate_eligibility()
            visits._validate_time_windows()
            visits._apply_transition({'state': 'planned'})
            super(SalesVisitPlan, plan).write({
                'state': 'confirmed', 'confirmed_at': fields.Datetime.now(), 'confirmed_by': self.env.user.id,
            })
        return True

    def _validate_sequences(self, visits):
        sequences = visits.mapped('sequence')
        if len(sequences) != len(set(sequences)) or any(seq <= 0 for seq in sequences):
            raise ValidationError('Visit order must use unique positive sequence numbers.')

    def action_cancel(self, reason):
        self.check_access('write')
        if not reason or not reason.strip():
            raise ValidationError('A cancellation reason is required.')
        for plan in self:
            check_allowed_company(plan)
            if plan.state == 'cancelled':
                raise UserError('This plan is already cancelled.')
            if plan.user_id != self.env.user and not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
                raise AccessError('You can only cancel your own plan.')
            plan.visit_ids.filtered(lambda v: v.state in ('draft', 'planned', 'rescheduled'))._apply_transition({
                'state': 'cancelled', 'cancellation_reason': reason.strip(),
            })
            super(SalesVisitPlan, plan).write({
                'state': 'cancelled', 'cancellation_reason': reason.strip(),
                'cancelled_at': fields.Datetime.now(), 'cancelled_by': self.env.user.id,
            })
        return True

    def action_open_cancel(self):
        self.ensure_one()
        check_allowed_company(self)
        if self.user_id != self.env.user and not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
            raise AccessError('You can only cancel your own plan.')
        return {'type': 'ir.actions.act_window', 'res_model': 'gt.sales.plan.cancel.wizard',
                'view_mode': 'form', 'target': 'new', 'context': {'default_plan_id': self.id}}

    def unlink(self):
        if any(plan.state != 'draft' for plan in self):
            raise AccessError('Confirmed and cancelled plan history cannot be deleted.')
        for plan in self:
            check_allowed_company(plan)
            if plan.user_id != self.env.user and not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
                raise AccessError('You can only delete your own draft plans.')
        return super().unlink()


class SalesVisit(models.Model):
    _name = 'gt.sales.visit'
    _description = 'Planned Customer Visit'
    _order = 'sequence, id'
    _check_company_auto = True

    name = fields.Char(compute='_compute_name', store=True)
    plan_id = fields.Many2one('gt.sales.visit.plan', required=True, index=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one(related='plan_id.company_id', store=True, index=True)
    territory_id = fields.Many2one(related='plan_id.territory_id', store=True, index=True)
    user_id = fields.Many2one(related='plan_id.user_id', store=True, index=True)
    partner_id = fields.Many2one('res.partner', required=True, index=True, ondelete='restrict',
                                  domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    crm_lead_id = fields.Many2one('crm.lead', index=True, ondelete='set null', check_company=True,
                                  domain="[('partner_id', '=', partner_id), ('company_id', '=', company_id)]")
    sequence = fields.Integer(required=True, default=10, index=True)
    planned_date = fields.Date(related='plan_id.visit_date', store=True, index=True)
    planned_start_at = fields.Datetime()
    planned_end_at = fields.Datetime()
    state = fields.Selection([('draft', 'Draft'), ('planned', 'Planned'), ('rescheduled', 'Rescheduled'), ('cancelled', 'Cancelled')],
                             required=True, default='draft', index=True)
    planning_notes = fields.Text()
    reschedule_origin_id = fields.Many2one('gt.sales.visit', index=True, ondelete='restrict')
    reschedule_reason = fields.Text()
    cancellation_reason = fields.Text()

    _unique_active_plan_partner = models.UniqueIndex(
        '(plan_id, partner_id) WHERE state IN (\'draft\', \'planned\')',
        'A customer can appear only once among active visits in a plan.',
    )
    _unique_active_sequence = models.UniqueIndex(
        '(plan_id, sequence) WHERE state IN (\'draft\', \'planned\')',
        'Active visit sequence numbers must be unique within a plan.',
    )

    @api.depends('partner_id')
    def _compute_name(self):
        for visit in self:
            visit.name = visit.partner_id.display_name or 'Customer Visit'

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        for vals in vals_list:
            plan = self.env['gt.sales.visit.plan'].browse(vals.get('plan_id'))
            if not plan.exists() or plan.state != 'draft':
                raise ValidationError('Visits can only be added to a draft plan.')
            check_allowed_company(plan)
            self._check_plan_owner(plan)
        records = super().create(vals_list)
        records._validate_eligibility()
        records._validate_time_windows()
        return records

    def _check_plan_owner(self, plan):
        if plan.user_id != self.env.user and not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
            raise AccessError('You can only edit visits in your own plan.')

    def _validate_eligibility(self):
        for visit in self:
            plan = visit.plan_id
            check_allowed_company(plan)
            if plan.state != 'draft':
                raise UserError('Confirmed visits can only be changed by an authorized planning action.')
            self._check_plan_owner(plan)
            if visit.partner_id.company_id and visit.partner_id.company_id != plan.company_id:
                raise ValidationError('The customer belongs to another company.')
            visit.partner_id.check_access('read')
            mapping = self.env['gt.sales.territory.customer'].with_context(active_test=False).search([
                ('territory_id', '=', plan.territory_id.id), ('partner_id', '=', visit.partner_id.id),
                ('company_id', '=', plan.company_id.id), ('active', '=', True),
            ], limit=1)
            if not mapping:
                raise ValidationError('The customer must have an active allocation to the plan territory.')
            if visit.crm_lead_id:
                visit.crm_lead_id.check_access('read')
                if visit.crm_lead_id.company_id and visit.crm_lead_id.company_id != plan.company_id:
                    raise ValidationError('The opportunity belongs to another company.')
                if visit.crm_lead_id.partner_id != visit.partner_id:
                    raise ValidationError('The opportunity customer must match the planned customer.')
            if visit.sequence <= 0:
                raise ValidationError('Visit sequence must be a positive integer.')

    def _validate_time_windows(self):
        for visit in self:
            start, end = visit.planned_start_at, visit.planned_end_at
            if bool(start) != bool(end):
                raise ValidationError('Provide both the start and end time, or leave both empty.')
            if start and start >= end:
                raise ValidationError('The visit start time must precede its end time.')
            if start:
                timezone = pytz.timezone(visit.plan_id.timezone)
                local_start = fields.Datetime.context_timestamp(visit.with_context(tz=visit.plan_id.timezone), start)
                local_end = fields.Datetime.context_timestamp(visit.with_context(tz=visit.plan_id.timezone), end)
                if local_start.date() != visit.planned_date or local_end.date() != visit.planned_date:
                    raise ValidationError('Visit times must fall within the plan local date (%s).' % timezone.zone)
                conflict = self.search([
                    ('id', '!=', visit.id), ('user_id', '=', visit.user_id.id),
                    ('planned_date', '=', visit.planned_date), ('state', 'in', ['draft', 'planned']),
                    ('planned_start_at', '<', end), ('planned_end_at', '>', start),
                ], limit=1)
                if conflict:
                    raise ValidationError('This time window overlaps another visit for the salesperson.')

    def write(self, vals):
        protected = {'plan_id', 'partner_id', 'sequence', 'planned_start_at', 'planned_end_at', 'crm_lead_id'}
        for visit in self:
            check_allowed_company(visit.plan_id)
            self._check_plan_owner(visit.plan_id)
            if visit.state not in ('draft',):
                raise UserError('Only draft visits can be edited directly.')
            if 'state' in vals:
                raise AccessError('Use the plan action to change visit status.')
            if protected.intersection(vals) and visit.plan_id.state != 'draft':
                raise UserError('Visits in confirmed plans are immutable.')
        result = super().write(vals)
        self._validate_eligibility()
        self._validate_time_windows()
        return result

    def action_cancel(self, reason):
        if not reason or not reason.strip():
            raise ValidationError('A cancellation reason is required.')
        for visit in self:
            check_allowed_company(visit.plan_id)
            self._check_plan_owner(visit.plan_id)
            if visit.state not in ('draft', 'planned'):
                raise UserError('Only draft or planned visits can be cancelled.')
        return self._apply_transition({
            'state': 'cancelled', 'cancellation_reason': reason.strip(),
        })

    def action_open_reschedule(self):
        self.ensure_one()
        check_allowed_company(self.plan_id)
        self._check_plan_owner(self.plan_id)
        if self.state not in ('draft', 'planned'):
            raise UserError('Only draft or planned visits can be rescheduled.')
        return {'type': 'ir.actions.act_window', 'res_model': 'gt.sales.visit.reschedule.wizard',
                'view_mode': 'form', 'target': 'new', 'context': {'default_visit_id': self.id}}

    def action_reschedule(self, target_date, reason, target_plan=None):
        self.ensure_one()
        check_allowed_company(self.plan_id)
        self._check_plan_owner(self.plan_id)
        if self.state not in ('draft', 'planned'):
            raise UserError('Only draft or planned visits can be rescheduled.')
        if not reason or not reason.strip():
            raise ValidationError('A reschedule reason is required.')
        if target_plan and target_plan.state != 'draft':
            raise UserError('Reschedule into a draft plan; confirmed plans cannot be amended here.')
        if not target_plan:
            target_plan = self.env['gt.sales.visit.plan'].create({
                'company_id': self.company_id.id, 'user_id': self.user_id.id,
                'visit_date': target_date, 'timezone': self.plan_id.timezone,
                'territory_id': self.territory_id.id,
            })
        if (target_plan.company_id != self.company_id or target_plan.user_id != self.user_id
                or target_plan.visit_date != fields.Date.to_date(target_date)
                or target_plan.territory_id != self.territory_id):
            raise ValidationError('The target plan must match company, salesperson, territory, and date.')
        if target_plan.visit_ids.filtered(lambda v: v.partner_id == self.partner_id and v.state in ('draft', 'planned')):
            raise ValidationError('The customer already has an active visit in the target plan.')
        sequence = max(target_plan.visit_ids.mapped('sequence') or [0]) + 10
        replacement = self.create({
            'plan_id': target_plan.id, 'partner_id': self.partner_id.id, 'sequence': sequence,
            'crm_lead_id': self.crm_lead_id.id, 'planning_notes': self.planning_notes,
            'planned_start_at': False, 'planned_end_at': False,
            'reschedule_origin_id': self.id, 'reschedule_reason': reason.strip(),
        })
        self._apply_transition({'state': 'rescheduled', 'reschedule_reason': reason.strip()})
        return replacement

    def _apply_transition(self, vals):
        return super(SalesVisit, self).write(vals)

    def unlink(self):
        if any(v.state != 'draft' for v in self):
            raise AccessError('Historical visits cannot be deleted.')
        for visit in self:
            check_allowed_company(visit.plan_id)
            self._check_plan_owner(visit.plan_id)
            if visit.plan_id.state != 'draft':
                raise UserError('Visits can only be deleted from draft plans.')
        return super().unlink()
