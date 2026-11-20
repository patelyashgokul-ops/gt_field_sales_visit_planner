from datetime import datetime, time

import pytz

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .common import check_allowed_company


class SalesVisitOutcome(models.Model):
    _name = 'gt.sales.visit.outcome'
    _description = 'Field Visit Business Outcome'
    _order = 'sequence, name, id'
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True, default=lambda s: s.env.company)
    active = fields.Boolean(default=True, index=True)
    sequence = fields.Integer(default=10)
    description = fields.Text()
    requires_notes = fields.Boolean()
    requires_followup = fields.Boolean()
    followup_allowed = fields.Boolean(default=True)
    requires_objection = fields.Boolean()
    is_positive = fields.Boolean()
    is_unavailable = fields.Boolean()

    _code_company_unique = models.Constraint('UNIQUE(company_id, code)', 'Outcome codes must be unique per company.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
            raise AccessError('Only Field Sales managers can configure visit outcomes.')
        for vals in vals_list:
            company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
            if company not in self.env.companies:
                raise AccessError('You cannot configure outcomes for this company.')
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
            raise AccessError('Only Field Sales managers can configure visit outcomes.')
        for outcome in self:
            check_allowed_company(outcome)
            if outcome.company_id not in self.env.companies or (vals.get('company_id') and vals['company_id'] != outcome.company_id.id):
                raise AccessError('Outcome company cannot be changed or accessed outside your companies.')
        return super().write(vals)

    def unlink(self):
        raise AccessError('Visit outcomes are historical configuration; archive them instead.')


class SalesVisitBusiness(models.Model):
    _inherit = ['gt.sales.visit', 'mail.thread', 'mail.activity.mixin']

    state = fields.Selection(selection_add=[('missed', 'Missed'), ('completed', 'Completed')],
                             ondelete={'missed': 'set default', 'completed': 'set default'})
    outcome_id = fields.Many2one('gt.sales.visit.outcome', check_company=True, ondelete='restrict', index=True)
    meeting_summary = fields.Text()
    customer_feedback = fields.Text()
    customer_objection = fields.Text()
    product_interest_ids = fields.One2many('gt.sales.visit.product.interest', 'visit_id')
    followup_required = fields.Boolean()
    next_followup_date = fields.Date()
    next_followup_user_id = fields.Many2one('res.users', check_company=True)
    next_followup_summary = fields.Char()
    followup_activity_id = fields.Many2one('mail.activity', readonly=True, copy=False, ondelete='set null')
    followup_completed_at = fields.Datetime(readonly=True, copy=False)
    followup_status = fields.Selection([
        ('none', 'No Follow-up'), ('scheduled', 'Scheduled'), ('due', 'Due Today'),
        ('overdue', 'Overdue'), ('completed', 'Completed'), ('cancelled', 'Cancelled / Removed'),
    ], compute='_compute_followup_status')
    completed_at = fields.Datetime(readonly=True, copy=False, index=True)
    completed_by = fields.Many2one('res.users', readonly=True, copy=False)
    outcome_snapshot = fields.Json(readonly=True, copy=False)
    missed_reason = fields.Text(copy=False)
    missed_at = fields.Datetime(readonly=True, copy=False)
    missed_by = fields.Many2one('res.users', readonly=True, copy=False)

    @api.depends('followup_required', 'followup_completed_at', 'followup_activity_id', 'followup_activity_id.active',
                 'followup_activity_id.date_deadline', 'followup_activity_id.state')
    def _compute_followup_status(self):
        for visit in self:
            activity = visit.followup_activity_id
            if not visit.followup_required:
                visit.followup_status = 'none'
            elif visit.followup_completed_at:
                visit.followup_status = 'completed'
            elif not activity:
                visit.followup_status = 'cancelled'
            elif not activity.active:
                visit.followup_status = 'cancelled'
            elif activity.state == 'done':
                visit.followup_status = 'completed'
            else:
                local_today = fields.Date.context_today(visit.with_context(tz=visit.plan_id.timezone))
                visit.followup_status = 'overdue' if activity.date_deadline < local_today else (
                    'due' if activity.date_deadline == local_today else 'scheduled')

    def _check_business_actor(self):
        self.ensure_one()
        check_allowed_company(self.plan_id)
        if self.company_id not in self.env.companies:
            raise AccessError('You cannot access this visit company.')
        if self.env.user != self.user_id and not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
            raise AccessError('Only the assigned salesperson or an authorized manager can update this visit.')

    def _validate_business_values(self, values=None):
        values = values or {}
        outcome = self.env['gt.sales.visit.outcome'].browse(values.get('outcome_id') or self.outcome_id.id)
        if outcome:
            if not outcome.exists() or outcome.company_id != self.company_id or not outcome.active:
                raise ValidationError('Choose an active outcome configured for this visit company.')
            summary = values.get('meeting_summary', self.meeting_summary)
            objection = values.get('customer_objection', self.customer_objection)
            followup = values.get('followup_required', self.followup_required)
            if outcome.requires_notes and not (summary or '').strip():
                raise ValidationError('This outcome requires a meaningful meeting summary.')
            if outcome.requires_objection and not (objection or '').strip():
                raise ValidationError('This outcome requires an objection description.')
            if followup and not outcome.followup_allowed:
                raise ValidationError('This outcome does not allow a follow-up activity.')
            if outcome.requires_followup and not followup:
                raise ValidationError('This outcome requires a follow-up.')
        followup = values.get('followup_required', self.followup_required)
        if followup:
            due = fields.Date.to_date(values.get('next_followup_date') or self.next_followup_date)
            assignee = self.env['res.users'].browse(values.get('next_followup_user_id') or self.next_followup_user_id.id)
            summary = values.get('next_followup_summary', self.next_followup_summary)
            if not due or not assignee or not assignee.exists() or not (summary or '').strip():
                raise ValidationError('Follow-up date, responsible user, and summary are required.')
            if assignee.share or not assignee.has_group('base.group_user') or self.company_id not in assignee.company_ids or not assignee.active:
                raise ValidationError('The follow-up owner must be an active internal user in this company.')
            completion_time = self.completed_at or fields.Datetime.now()
            timezone = pytz.timezone(self.plan_id.timezone)
            local_completion_date = pytz.UTC.localize(completion_time).astimezone(timezone).date()
            if due < local_completion_date:
                raise ValidationError('Follow-up date cannot be earlier than the visit completion date.')
        for line in self.product_interest_ids:
            if line.company_id != self.company_id:
                raise ValidationError('Product interest must belong to the visit company.')
        if self.crm_lead_id:
            self.crm_lead_id.check_access('read')
            if self.crm_lead_id.company_id and self.crm_lead_id.company_id != self.company_id:
                raise ValidationError('The linked opportunity belongs to another company.')
            if self.crm_lead_id.partner_id != self.partner_id:
                raise ValidationError('The opportunity customer must match this visit customer.')

    def write(self, vals):
        protected = {'completed_at', 'completed_by', 'missed_at', 'missed_by', 'outcome_snapshot',
                     'followup_activity_id'}
        if protected.intersection(vals):
            raise AccessError('Completion and follow-up metadata can only be changed by visit actions.')
        business = {'outcome_id', 'meeting_summary', 'customer_feedback', 'customer_objection',
                    'followup_required', 'next_followup_date', 'next_followup_user_id', 'next_followup_summary'}
        for visit in self:
            visit._check_business_actor()
            if visit.state == 'completed' and business.intersection(vals):
                raise AccessError('Completed business results require the manager correction action.')
            if visit.state not in ('checked_out_pending_outcome', 'completed') and business.intersection(vals):
                raise UserError('Business results can be entered after checkout.')
            if business.intersection(vals):
                visit._validate_business_values(vals)
        return super().write(vals)

    def action_complete_visit(self):
        self.ensure_one()
        self.check_access('write')
        self._check_business_actor()
        self._lock_for_planning_transition()
        if self.state == 'completed':
            return {'visit_id': self.id, 'state': 'completed', 'completed_at': fields.Datetime.to_string(self.completed_at),
                    'activity_id': self.followup_activity_id.id or False}
        if self.state != 'checked_out_pending_outcome':
            raise UserError('Only checked-out visits can be completed.')
        if not self.checkin_event_id or not self.checkout_event_id or not self.checkin_at or not self.checkout_at:
            raise ValidationError('A valid check-in and check-out are required before completion.')
        if self.checkout_at < self.checkin_at:
            raise ValidationError('Checkout cannot precede check-in.')
        self._validate_business_values()
        if not self.outcome_id:
            raise ValidationError('Choose a meeting outcome before completing the visit.')
        now = fields.Datetime.now()
        activity = self.env['mail.activity']
        if self.followup_required:
            todo_type = self.env.ref('mail.mail_activity_data_todo', raise_if_not_found=False)
            if not todo_type:
                raise ValidationError('The standard To Do activity type is unavailable.')
            activity = self.env['mail.activity'].sudo().create({
                'res_model_id': self.env['ir.model']._get_id('gt.sales.visit'), 'res_id': self.id,
                'activity_type_id': todo_type.id, 'summary': self.next_followup_summary.strip(),
                'note': self.next_followup_summary.strip(), 'date_deadline': self.next_followup_date,
                'user_id': self.next_followup_user_id.id, 'gt_source_visit_id': self.id,
            })
        snapshot = {'name': self.outcome_id.name, 'code': self.outcome_id.code,
                    'requires_notes': self.outcome_id.requires_notes,
                    'requires_followup': self.outcome_id.requires_followup,
                    'followup_allowed': self.outcome_id.followup_allowed,
                    'is_positive': self.outcome_id.is_positive, 'is_unavailable': self.outcome_id.is_unavailable}
        models.Model.write(self, {'state': 'completed', 'completed_at': now, 'completed_by': self.env.user.id,
                                  'outcome_snapshot': snapshot, 'followup_activity_id': activity.id or False})
        self.env['gt.sales.visit.event'].sudo().create({
            'visit_id': self.id, 'company_id': self.company_id.id, 'actor_user_id': self.env.user.id,
            'event_type': 'visit_completed', 'occurred_at': now, 'location_status': 'not_required',
            'policy_snapshot': {'outcome': snapshot, 'followup_required': self.followup_required},
            'explanation': (self.meeting_summary or '')[:2000],
        })
        return {'visit_id': self.id, 'state': 'completed', 'completed_at': fields.Datetime.to_string(now),
                'activity_id': activity.id or False}

    def action_mark_missed(self, reason):
        self.ensure_one()
        self.check_access('write')
        self._check_business_actor()
        if not reason or not reason.strip():
            raise ValidationError('A reason is required to mark a visit missed.')
        self._lock_for_planning_transition()
        if self.state != 'planned':
            raise UserError('Only unexecuted planned visits can be marked missed.')
        now = fields.Datetime.now()
        models.Model.write(self, {'state': 'missed', 'missed_reason': reason.strip(),
                                  'missed_at': now, 'missed_by': self.env.user.id})
        self.env['gt.sales.visit.event'].sudo().create({
            'visit_id': self.id, 'company_id': self.company_id.id, 'actor_user_id': self.env.user.id,
            'event_type': 'visit_missed', 'occurred_at': now, 'location_status': 'not_required',
            'policy_snapshot': {'missed': True}, 'explanation': reason.strip(),
        })
        return True

    def action_open_missed(self):
        self.ensure_one()
        self._check_business_actor()
        if self.state != 'planned':
            raise UserError('Only unexecuted planned visits can be marked missed.')
        return {'type': 'ir.actions.act_window', 'res_model': 'gt.sales.visit.missed.wizard',
                'view_mode': 'form', 'target': 'new', 'context': {'default_visit_id': self.id}}

    def action_open_correction(self):
        self.ensure_one()
        self._check_business_actor()
        if self.state != 'completed' or not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
            raise AccessError('Only a Sales Manager can correct a completed visit.')
        return {'type': 'ir.actions.act_window', 'res_model': 'gt.sales.visit.correction.wizard',
                'view_mode': 'form', 'target': 'new', 'context': {'default_visit_id': self.id}}

    def action_correct_business_result(self, values, reason):
        self.ensure_one()
        self.check_access('write')
        self._check_business_actor()
        if not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
            raise AccessError('Only a Sales Manager can correct completed business results.')
        if self.state != 'completed' or not isinstance(values, dict) or not reason or not reason.strip():
            raise ValidationError('A completed visit, correction values, and reason are required.')
        allowed = {'outcome_id', 'meeting_summary', 'customer_feedback', 'customer_objection',
                   'followup_required', 'next_followup_date', 'next_followup_user_id', 'next_followup_summary'}
        if not values or set(values) - allowed:
            raise ValidationError('The correction contains unsupported fields.')
        before = {key: self[key].id if self._fields[key].type == 'many2one' else self[key]
                  for key in values}
        self._validate_business_values(values)
        if values.get('outcome_id'):
            out = self.env['gt.sales.visit.outcome'].browse(values['outcome_id'])
            snapshot = {'name': out.name, 'code': out.code, 'requires_notes': out.requires_notes,
                        'requires_followup': out.requires_followup, 'followup_allowed': out.followup_allowed,
                        'is_positive': out.is_positive, 'is_unavailable': out.is_unavailable}
            values = dict(values, outcome_snapshot=snapshot)
        models.Model.write(self, values)
        activity = self.followup_activity_id
        if activity:
            if not values.get('followup_required', self.followup_required):
                activity.write({'active': False})
            elif any(key in values for key in ('next_followup_date', 'next_followup_user_id', 'next_followup_summary')):
                activity.write({
                    'date_deadline': values.get('next_followup_date', self.next_followup_date),
                    'user_id': values.get('next_followup_user_id', self.next_followup_user_id.id),
                    'summary': values.get('next_followup_summary', self.next_followup_summary),
                })
        self.env['gt.sales.visit.event'].sudo().create({
            'visit_id': self.id, 'company_id': self.company_id.id, 'actor_user_id': self.env.user.id,
            'event_type': 'business_result_corrected', 'occurred_at': fields.Datetime.now(),
            'location_status': 'not_required', 'policy_snapshot': {'before': before, 'after': values},
            'explanation': reason.strip(),
        })
        return True


class SalesVisitProductInterest(models.Model):
    _name = 'gt.sales.visit.product.interest'
    _description = 'Visit Product Interest'
    _order = 'id'
    _check_company_auto = True

    visit_id = fields.Many2one('gt.sales.visit', required=True, index=True, ondelete='cascade', check_company=True)
    company_id = fields.Many2one(related='visit_id.company_id', store=True, index=True)
    product_name = fields.Char(required=True)
    interest_level = fields.Selection([('low', 'Low'), ('medium', 'Medium'), ('high', 'High')], default='medium', required=True)
    notes = fields.Text()

    _unique_product_interest = models.UniqueIndex('(visit_id, product_name)', 'A product can be recorded once per visit.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            visit = self.env['gt.sales.visit'].browse(vals.get('visit_id'))
            visit._check_business_actor()
            if visit.state != 'checked_out_pending_outcome':
                raise UserError('Product interests can only be entered before visit completion.')
        return super().create(vals_list)

    def write(self, vals):
        for line in self:
            line.visit_id._check_business_actor()
            if line.visit_id.state != 'checked_out_pending_outcome':
                raise AccessError('Product interests on completed visits are protected.')
        return super().write(vals)


class MailActivityVisitFollowup(models.Model):
    _inherit = 'mail.activity'

    gt_source_visit_id = fields.Many2one('gt.sales.visit', index=True, copy=False, ondelete='restrict')

    _one_followup_per_source = models.UniqueIndex('(gt_source_visit_id) WHERE gt_source_visit_id IS NOT NULL',
                                                  'Only one Field Sales follow-up is allowed per source visit.')

    @api.model_create_multi
    def create(self, vals_list):
        if any(vals.get('gt_source_visit_id') for vals in vals_list) and not self.env.su:
            raise AccessError('Field Sales follow-ups are created by the visit completion workflow.')
        for vals in vals_list:
            if vals.get('gt_source_visit_id'):
                visit = self.env['gt.sales.visit'].sudo().browse(vals['gt_source_visit_id'])
                if not visit.exists() or vals.get('res_model_id') != self.env['ir.model']._get_id('gt.sales.visit') or vals.get('res_id') != visit.id:
                    raise ValidationError('The follow-up must target its source visit.')
                assignee = self.env['res.users'].sudo().browse(vals.get('user_id'))
                if visit.company_id not in assignee.company_ids or assignee.share or not assignee.active:
                    raise ValidationError('Follow-up owner must be an active internal user in the visit company.')
        return super().create(vals_list)

    def write(self, vals):
        sourced = self.filtered('gt_source_visit_id')
        if sourced:
            if {'gt_source_visit_id', 'res_model_id', 'res_id'} & vals.keys():
                raise AccessError('A Field Sales follow-up source cannot be changed.')
            if not (self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager') or
                    all(act.gt_source_visit_id.user_id == self.env.user or act.user_id == self.env.user for act in sourced)):
                raise AccessError('Only the assigned salesperson or an authorized manager can change this follow-up.')
            for activity in sourced:
                check_allowed_company(activity.gt_source_visit_id.plan_id)
                if vals.get('user_id'):
                    user = self.env['res.users'].browse(vals['user_id'])
                    if user.share or not user.active or activity.gt_source_visit_id.company_id not in user.company_ids:
                        raise ValidationError('Follow-up owner must be an active internal user in the visit company.')
            if 'date_deadline' in vals:
                for activity in sourced:
                    visit = activity.gt_source_visit_id
                    if fields.Date.to_date(vals['date_deadline']) < fields.Date.to_date(visit.completed_at):
                        raise ValidationError('Follow-up date cannot precede visit completion.')
        before = {}
        if sourced:
            before = {activity.id: {'user_id': activity.user_id.id, 'date_deadline': fields.Date.to_string(activity.date_deadline),
                                    'summary': activity.summary} for activity in sourced}
        result = super().write(vals)
        for activity in sourced:
            visit = activity.gt_source_visit_id
            event_type = 'followup_reassigned' if 'user_id' in vals else (
                'followup_deadline_changed' if 'date_deadline' in vals else 'business_result_corrected')
            self.env['gt.sales.visit.event'].sudo().create({
                'visit_id': visit.id, 'company_id': visit.company_id.id, 'actor_user_id': self.env.user.id,
                'event_type': event_type, 'occurred_at': fields.Datetime.now(), 'location_status': 'not_required',
                'policy_snapshot': {'before': before[activity.id], 'after': {
                    'user_id': activity.user_id.id, 'date_deadline': fields.Date.to_string(activity.date_deadline),
                    'summary': activity.summary}},
            })
        return result

    def action_feedback(self, feedback=False, attachment_ids=None):
        for activity in self.filtered('gt_source_visit_id'):
            visit = activity.gt_source_visit_id
            visit._check_business_actor()
            completed_at = fields.Datetime.now()
            self.env['gt.sales.visit.event'].sudo().create({
                'visit_id': visit.id, 'company_id': visit.company_id.id,
                'actor_user_id': self.env.user.id, 'event_type': 'followup_completed',
                'occurred_at': completed_at, 'location_status': 'not_required',
                'policy_snapshot': {'activity_id': activity.id}, 'explanation': feedback or '',
            })
            models.Model.write(visit, {'followup_completed_at': completed_at})
        return super().action_feedback(feedback=feedback, attachment_ids=attachment_ids)

    def unlink(self):
        if self.filtered('gt_source_visit_id') and not self.env.context.get('gt_followup_done'):
            raise AccessError('Use the standard Mark Done action; source follow-up history cannot be deleted.')
        return super().unlink()


class SalesVisitMissedWizard(models.TransientModel):
    _name = 'gt.sales.visit.missed.wizard'
    _description = 'Mark Field Visit Missed'

    visit_id = fields.Many2one('gt.sales.visit', required=True, readonly=True)
    reason = fields.Text(required=True)

    def action_apply(self):
        self.ensure_one()
        self.visit_id.action_mark_missed(self.reason)
        return {'type': 'ir.actions.act_window_close'}


class SalesVisitCorrectionWizard(models.TransientModel):
    _name = 'gt.sales.visit.correction.wizard'
    _description = 'Correct Visit Business Result'

    visit_id = fields.Many2one('gt.sales.visit', required=True, readonly=True)
    outcome_id = fields.Many2one('gt.sales.visit.outcome', check_company=True)
    meeting_summary = fields.Text()
    customer_feedback = fields.Text()
    customer_objection = fields.Text()
    followup_required = fields.Boolean()
    next_followup_date = fields.Date()
    next_followup_user_id = fields.Many2one('res.users', check_company=True)
    next_followup_summary = fields.Char()
    reason = fields.Text(required=True)

    def action_apply(self):
        self.ensure_one()
        values = {key: self[key].id if self._fields[key].type == 'many2one' else self[key]
                  for key in ('outcome_id', 'meeting_summary', 'customer_feedback', 'customer_objection',
                              'followup_required', 'next_followup_date', 'next_followup_user_id', 'next_followup_summary')
                  if self[key]}
        self.visit_id.action_correct_business_result(values, self.reason)
        return {'type': 'ir.actions.act_window_close'}


class SalesVisitBusinessEvent(models.Model):
    _inherit = 'gt.sales.visit.event'

    event_type = fields.Selection(selection_add=[
        ('visit_completed', 'Visit Completed'), ('visit_missed', 'Visit Marked Missed'),
        ('business_result_corrected', 'Business Result Corrected'),
        ('followup_completed', 'Follow-up Completed'), ('followup_cancelled', 'Follow-up Cancelled'),
        ('followup_reassigned', 'Follow-up Reassigned'), ('followup_deadline_changed', 'Follow-up Deadline Changed'),
    ], ondelete={
        'visit_completed': 'cascade', 'visit_missed': 'cascade', 'business_result_corrected': 'cascade',
        'followup_completed': 'cascade', 'followup_cancelled': 'cascade',
        'followup_reassigned': 'cascade', 'followup_deadline_changed': 'cascade',
    })
