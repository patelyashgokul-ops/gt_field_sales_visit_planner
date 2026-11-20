import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone

import pytz
from psycopg2.errors import ExclusionViolation, UniqueViolation

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .common import check_allowed_company


CHECKIN = 'check_in'
CHECKOUT = 'check_out'


class SalesExecutionPolicy(models.Model):
    _name = 'gt.sales.execution.policy'
    _description = 'Field Visit GPS Policy'
    _check_company_auto = True

    name = fields.Char(default='GPS Policy', required=True)
    company_id = fields.Many2one('res.company', required=True, index=True, ondelete='cascade')
    require_checkin_gps = fields.Boolean(default=True)
    require_checkout_gps = fields.Boolean(default=True)
    default_radius_m = fields.Float(default=100.0, required=True)
    max_radius_m = fields.Float(default=5000.0, required=True)
    max_accuracy_m = fields.Float(default=50.0, required=True)
    missing_policy = fields.Selection([('block', 'Block'), ('review', 'Allow with review')], default='block', required=True)
    outside_policy = fields.Selection([('block', 'Block'), ('review', 'Allow with review')], default='block', required=True)
    low_accuracy_policy = fields.Selection([('block', 'Block'), ('review', 'Allow with review')], default='review', required=True)
    uncertain_policy = fields.Selection([('block', 'Block'), ('review', 'Allow with review')], default='review', required=True)
    not_configured_policy = fields.Selection([('block', 'Block'), ('review', 'Allow with review')], default='block', required=True)
    checkin_early_tolerance_min = fields.Integer(default=0)
    checkin_late_tolerance_min = fields.Integer(default=0)

    _company_policy_unique = models.Constraint('UNIQUE(company_id)', 'Only one GPS policy is allowed per company.')

    @api.constrains('default_radius_m', 'max_radius_m', 'max_accuracy_m', 'checkin_early_tolerance_min', 'checkin_late_tolerance_min')
    def _check_policy_values(self):
        for policy in self:
            if (not math.isfinite(policy.default_radius_m) or not math.isfinite(policy.max_radius_m)
                    or not math.isfinite(policy.max_accuracy_m) or policy.default_radius_m <= 0
                    or policy.max_radius_m < policy.default_radius_m or policy.max_radius_m > 100000
                    or policy.max_accuracy_m <= 0 or policy.max_accuracy_m > 100000
                    or policy.checkin_early_tolerance_min < 0 or policy.checkin_late_tolerance_min < 0
                    or policy.checkin_early_tolerance_min > 1440 or policy.checkin_late_tolerance_min > 1440):
                raise ValidationError('GPS radii, accuracy and date tolerances must be within supported limits.')

    @api.model_create_multi
    def create(self, vals_list):
        if not (self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager')
                or self.env.user.has_group('gt_field_sales_visit_planner.group_field_sales_admin')):
            raise AccessError('Only a Field Sales Manager can configure GPS policy.')
        for vals in vals_list:
            company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
            if not company.exists() or company not in self.env.companies:
                raise AccessError('You cannot configure GPS policy for this company.')
            vals['company_id'] = company.id
        return super().create(vals_list)

    def write(self, vals):
        if not (self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager')
                or self.env.user.has_group('gt_field_sales_visit_planner.group_field_sales_admin')):
            raise AccessError('Only a Field Sales Manager can configure GPS policy.')
        for record in self:
            check_allowed_company(record)
        if 'company_id' in vals and any(record.company_id.id != vals['company_id'] for record in self):
            raise UserError('GPS policy company ownership cannot be changed.')
        return super().write(vals)


class CustomerVisitLocation(models.Model):
    _name = 'gt.sales.customer.location'
    _description = 'Approved Customer Visit Location'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'partner_id, name, id'
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one('res.company', required=True, index=True, ondelete='cascade', tracking=True)
    partner_id = fields.Many2one('res.partner', required=True, index=True, ondelete='restrict', tracking=True)
    latitude = fields.Float(required=True, digits=(10, 7), tracking=True)
    longitude = fields.Float(required=True, digits=(10, 7), tracking=True)
    geofence_radius_m = fields.Float(required=True, default=100.0, tracking=True)
    active = fields.Boolean(default=True, index=True, tracking=True)
    approved_by = fields.Many2one('res.users', readonly=True, copy=False, tracking=True)
    approved_at = fields.Datetime(readonly=True, copy=False, tracking=True)
    notes = fields.Text()

    _primary_company_customer = models.UniqueIndex(
        '(company_id, partner_id) WHERE active IS TRUE',
        'Only one active approved location is allowed for a customer per company.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        self._check_location_manager()
        for vals in vals_list:
            vals['company_id'] = self._validate_company(vals.get('company_id'))
            self._validate_partner(vals.get('partner_id'), vals['company_id'])
            self._validate_coordinates(vals.get('latitude'), vals.get('longitude'))
            self._validate_radius(vals.get('geofence_radius_m'))
            vals['approved_by'] = self.env.user.id
            vals['approved_at'] = fields.Datetime.now()
        with self.env.cr.savepoint():
            return super().create(vals_list)

    def write(self, vals):
        self._check_location_manager()
        for location in self:
            check_allowed_company(location)
            company_id = vals.get('company_id', location.company_id.id)
            partner_id = vals.get('partner_id', location.partner_id.id)
            self._validate_company(company_id)
            self._validate_partner(partner_id, company_id)
            self._validate_coordinates(vals.get('latitude', location.latitude), vals.get('longitude', location.longitude))
            self._validate_radius(vals.get('geofence_radius_m', location.geofence_radius_m))
        vals = dict(vals, approved_by=self.env.user.id, approved_at=fields.Datetime.now())
        return super().write(vals)

    def unlink(self):
        self._check_location_manager()
        if self.env['gt.sales.visit.event'].search_count([('reference_location_id', 'in', self.ids)]):
            raise UserError('Archive a location that has historical visit evidence.')
        return super().unlink()

    def _check_location_manager(self):
        if not (self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager')
                or self.env.user.has_group('gt_field_sales_visit_planner.group_field_sales_admin')):
            raise AccessError('Only a Sales Manager can maintain approved customer visit locations.')

    def _validate_company(self, company_id):
        company = self.env['res.company'].browse(company_id or self.env.company.id)
        if not company.exists() or company not in self.env.companies:
            raise AccessError('The customer location company is not authorized.')
        return company.id

    def _validate_partner(self, partner_id, company_id):
        partner = self.env['res.partner'].browse(partner_id)
        if not partner.exists():
            raise ValidationError('Select a valid customer.')
        partner.check_access('read')
        if partner.company_id and partner.company_id.id != company_id:
            raise ValidationError('The customer belongs to another company.')

    @staticmethod
    def _validate_coordinates(latitude, longitude):
        try:
            lat, lon = float(latitude), float(longitude)
        except (TypeError, ValueError):
            raise ValidationError('Enter valid latitude and longitude values.')
        if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
            raise ValidationError('Latitude or longitude is outside its valid range.')

    @staticmethod
    def _validate_radius(radius):
        try:
            radius = float(radius)
        except (TypeError, ValueError):
            raise ValidationError('Enter a valid geofence radius.')
        if not math.isfinite(radius) or radius <= 0 or radius > 100000:
            raise ValidationError('Geofence radius must be greater than zero and at most 100 km.')


class SalesVisitEvent(models.Model):
    _name = 'gt.sales.visit.event'
    _description = 'Immutable Field Visit Evidence'
    _order = 'occurred_at, id'
    _check_company_auto = True

    visit_id = fields.Many2one('gt.sales.visit', required=True, index=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one('res.company', required=True, index=True, ondelete='restrict')
    actor_user_id = fields.Many2one('res.users', required=True, index=True, ondelete='restrict')
    event_type = fields.Selection([
        ('check_in', 'Check In'), ('check_out', 'Check Out'),
        ('exception_approved', 'Location Exception Approved'),
        ('exception_rejected', 'Location Exception Rejected'), ('manager_recovery', 'Manager Recovery'),
    ], required=True, index=True)
    occurred_at = fields.Datetime(required=True, index=True)
    client_acquired_at = fields.Datetime()
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    accuracy_m = fields.Float()
    reference_location_id = fields.Many2one('gt.sales.customer.location', ondelete='set null', check_company=True)
    reference_latitude = fields.Float(digits=(10, 7))
    reference_longitude = fields.Float(digits=(10, 7))
    reference_radius_m = fields.Float()
    distance_m = fields.Float()
    location_status = fields.Selection([
        ('within_radius', 'Within Configured Radius'), ('outside_radius', 'Outside Configured Radius'),
        ('low_accuracy', 'Low GPS Accuracy'), ('uncertain', 'Location Review Required'),
        ('missing', 'Missing GPS'), ('not_configured', 'Customer Location Not Configured'),
        ('invalid', 'Invalid GPS'), ('not_required', 'GPS Not Required'),
    ], required=True, index=True)
    policy_snapshot = fields.Json(required=True)
    request_token = fields.Char(index=True)
    explanation = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError('Visit evidence is recorded only by authorized execution actions.')
        for vals in vals_list:
            visit = self.env['gt.sales.visit'].browse(vals.get('visit_id'))
            if not visit.exists() or visit.company_id.id != vals.get('company_id'):
                raise ValidationError('Evidence must belong to its visit company.')
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError('Visit evidence is immutable. Record a separate explanatory event instead.')

    def unlink(self):
        raise AccessError('Visit evidence cannot be deleted.')


class SalesVisitLocationException(models.Model):
    _name = 'gt.sales.visit.location.exception'
    _description = 'Visit Location Exception'
    _order = 'create_date desc, id desc'
    _check_company_auto = True

    visit_id = fields.Many2one('gt.sales.visit', required=True, index=True, ondelete='restrict', check_company=True)
    event_id = fields.Many2one('gt.sales.visit.event', required=True, index=True, ondelete='restrict', check_company=True)
    company_id = fields.Many2one('res.company', required=True, index=True, ondelete='restrict')
    user_id = fields.Many2one('res.users', required=True, index=True, ondelete='restrict')
    event_type = fields.Selection([('check_in', 'Check In'), ('check_out', 'Check Out')], required=True)
    reason_code = fields.Selection([
        ('missing', 'Missing GPS'), ('outside_radius', 'Outside Radius'), ('low_accuracy', 'Low Accuracy'),
        ('uncertain', 'Location Uncertain'), ('not_configured', 'No Customer Location'), ('invalid', 'Invalid GPS'),
    ], required=True, index=True)
    explanation = fields.Text()
    location_status = fields.Selection(related='event_id.location_status', store=True, index=True)
    review_state = fields.Selection([('pending', 'Pending Review'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', required=True, index=True)
    reviewer_id = fields.Many2one('res.users', readonly=True, copy=False)
    reviewed_at = fields.Datetime(readonly=True, copy=False)
    review_reason = fields.Text(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError('Location exceptions are created only by execution policy.')
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError('Use the exception review action to record a manager decision.')

    def unlink(self):
        raise AccessError('Location exception history cannot be deleted.')

    def action_review(self, decision, reason):
        if decision not in ('approved', 'rejected') or not reason or not reason.strip():
            raise ValidationError('Choose approve or reject and provide a review reason.')
        self.check_access('write')
        for exception in self:
            check_allowed_company(exception)
            if not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
                raise AccessError('Only a Sales Manager can review location exceptions.')
            if exception.user_id == self.env.user:
                raise AccessError('You cannot review your own location exception.')
            exception._lock_review()
            if exception.review_state != 'pending':
                raise UserError('This location exception has already been reviewed.')
            super(SalesVisitLocationException, exception).write({
                'review_state': decision, 'reviewer_id': self.env.user.id,
                'reviewed_at': fields.Datetime.now(), 'review_reason': reason.strip(),
            })
            self.env['gt.sales.visit.event'].sudo().create({
                'visit_id': exception.visit_id.id, 'company_id': exception.company_id.id,
                'actor_user_id': self.env.user.id,
                'event_type': 'exception_approved' if decision == 'approved' else 'exception_rejected',
                'occurred_at': fields.Datetime.now(), 'location_status': exception.location_status,
                'policy_snapshot': {'review': decision}, 'explanation': reason.strip(),
            })
        return True

    def action_open_approve(self):
        self.ensure_one()
        check_allowed_company(self)
        if self.review_state != 'pending':
            raise UserError('Only pending exceptions can be reviewed.')
        if not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
            raise AccessError('Only a Sales Manager can review location exceptions.')
        return {'type': 'ir.actions.act_window', 'res_model': 'gt.sales.visit.exception.review.wizard',
                'view_mode': 'form', 'target': 'new',
                'context': {'default_exception_id': self.id, 'default_decision': 'approved'}}

    def action_open_reject(self):
        action = self.action_open_approve()
        action['context'] = dict(action['context'], default_decision='rejected')
        return action

    def _lock_review(self):
        self.env.cr.execute('SELECT id FROM gt_sales_visit_location_exception WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['review_state', 'reviewer_id', 'reviewed_at', 'review_reason'])


class SalesVisitExecutionRequest(models.Model):
    _name = 'gt.sales.visit.execution.request'
    _description = 'Visit Execution Idempotency Key'
    _check_company_auto = True

    company_id = fields.Many2one('res.company', required=True, index=True, ondelete='restrict')
    visit_id = fields.Many2one('gt.sales.visit', required=True, index=True, ondelete='restrict', check_company=True)
    user_id = fields.Many2one('res.users', required=True, index=True, ondelete='restrict')
    event_type = fields.Selection([('check_in', 'Check In'), ('check_out', 'Check Out')], required=True)
    request_token = fields.Char(required=True)
    payload_hash = fields.Char(required=True)
    event_id = fields.Many2one('gt.sales.visit.event', required=True, ondelete='restrict')

    _request_token_unique = models.Constraint('UNIQUE(company_id, request_token)', 'This execution request token has already been used.')

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError('Execution request keys are created only by authorized execution actions.')
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError('Execution request keys are immutable.')

    def unlink(self):
        raise AccessError('Execution request keys cannot be deleted.')


class SalesVisitExecution(models.Model):
    _inherit = 'gt.sales.visit'

    state = fields.Selection(selection_add=[
        ('checked_in', 'Checked In'), ('checked_out_pending_outcome', 'Checked Out — Pending Outcome'),
    ], ondelete={'checked_in': 'set default', 'checked_out_pending_outcome': 'set default'})
    checkin_at = fields.Datetime(readonly=True, copy=False, index=True)
    checkin_latitude = fields.Float(readonly=True, copy=False, digits=(10, 7))
    checkin_longitude = fields.Float(readonly=True, copy=False, digits=(10, 7))
    checkin_accuracy_m = fields.Float(readonly=True, copy=False)
    checkin_location_status = fields.Selection(related='checkin_event_id.location_status', store=True, index=True)
    checkin_event_id = fields.Many2one('gt.sales.visit.event', readonly=True, copy=False, ondelete='restrict')
    checkout_at = fields.Datetime(readonly=True, copy=False, index=True)
    checkout_latitude = fields.Float(readonly=True, copy=False, digits=(10, 7))
    checkout_longitude = fields.Float(readonly=True, copy=False, digits=(10, 7))
    checkout_accuracy_m = fields.Float(readonly=True, copy=False)
    checkout_location_status = fields.Selection(related='checkout_event_id.location_status', store=True, index=True)
    checkout_event_id = fields.Many2one('gt.sales.visit.event', readonly=True, copy=False, ondelete='restrict')
    actual_duration_minutes = fields.Float(readonly=True, copy=False)
    checkin_exception_id = fields.Many2one('gt.sales.visit.location.exception', readonly=True, copy=False, ondelete='restrict')
    checkout_exception_id = fields.Many2one('gt.sales.visit.location.exception', readonly=True, copy=False, ondelete='restrict')
    checkin_policy_snapshot = fields.Json(readonly=True, copy=False)
    checkout_policy_snapshot = fields.Json(readonly=True, copy=False)
    execution_event_ids = fields.One2many('gt.sales.visit.event', 'visit_id', readonly=True)

    _one_active_checkin_per_salesperson = models.UniqueIndex(
        '(user_id) WHERE state = \'checked_in\'',
        'A salesperson can have only one active checked-in visit.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        protected = {
            'checkin_at', 'checkin_latitude', 'checkin_longitude', 'checkin_accuracy_m', 'checkin_event_id',
            'checkout_at', 'checkout_latitude', 'checkout_longitude', 'checkout_accuracy_m', 'checkout_event_id',
            'actual_duration_minutes', 'checkin_exception_id', 'checkout_exception_id',
            'checkin_policy_snapshot', 'checkout_policy_snapshot',
        }
        for vals in vals_list:
            if protected.intersection(vals) or vals.get('state') not in (None, 'draft'):
                raise AccessError('Execution history and visit execution states cannot be supplied during record creation.')
        return super().create(vals_list)

    def write(self, vals):
        protected = {
            'checkin_at', 'checkin_latitude', 'checkin_longitude', 'checkin_accuracy_m', 'checkin_event_id',
            'checkout_at', 'checkout_latitude', 'checkout_longitude', 'checkout_accuracy_m', 'checkout_event_id',
            'actual_duration_minutes', 'checkin_exception_id', 'checkout_exception_id',
            'checkin_policy_snapshot', 'checkout_policy_snapshot',
        }
        if protected.intersection(vals):
            raise AccessError('Execution evidence can only be changed by check-in, check-out, or recovery actions.')
        if 'state' in vals and set(vals) == {'state'}:
            if any(visit.state not in ('draft', 'planned') for visit in self):
                raise AccessError('Execution states can only be changed by authorized execution actions.')
        return super().write(vals)

    def _apply_transition(self, vals):
        # Private state transition hook; callers validate the transition before
        # this root ORM write. Public RPC cannot invoke underscore-prefixed methods.
        return models.Model.write(self, vals)

    def _lock_for_planning_transition(self):
        # Lock order: salesperson row, then visit row. Execution and P02 planning
        # transitions use the same order so cancellation/reschedule cannot race check-in.
        self.env.cr.execute('SELECT id FROM res_users WHERE id = %s FOR UPDATE', [self.user_id.id])
        self.env.cr.execute('SELECT id FROM gt_sales_visit WHERE id = %s FOR UPDATE', [self.id])
        self.invalidate_recordset(['state', 'plan_id', 'partner_id', 'reschedule_origin_id'])

    def copy(self, default=None):
        self.ensure_one()
        if self.state != 'draft' or self.checkin_at or self.checkout_at:
            raise AccessError('Visits with execution history cannot be duplicated.')
        return super().copy(default)

    def action_check_in(self, payload):
        return self._execute_location_event(CHECKIN, payload)

    def action_check_out(self, payload):
        return self._execute_location_event(CHECKOUT, payload)

    def _execute_location_event(self, event_type, payload):
        self.ensure_one()
        self.check_access('write')
        if not isinstance(payload, dict):
            raise ValidationError('A valid GPS capture request is required.')
        company = self.company_id
        check_allowed_company(self.plan_id)
        if company not in self.env.companies:
            raise AccessError('You cannot execute visits for this company.')
        if self.env.user != self.user_id or not self.env.user.has_group('gt_field_sales_visit_planner.group_field_salesperson'):
            raise AccessError('Only the assigned salesperson can execute this visit.')
        self._lock_for_planning_transition()
        self.env.cr.execute('SELECT id FROM res_company WHERE id = %s FOR SHARE', [company.id])
        self.env.cr.execute('SELECT id FROM gt_sales_execution_policy WHERE company_id = %s FOR SHARE', [company.id])
        policy_record = self.env['gt.sales.execution.policy'].sudo().search([('company_id', '=', company.id)], limit=1)
        location = self.env['gt.sales.customer.location'].sudo().search([
            ('company_id', '=', company.id), ('partner_id', '=', self.partner_id.id), ('active', '=', True),
        ], limit=1)
        if location:
            self.env.cr.execute('SELECT id FROM gt_sales_customer_location WHERE id = %s FOR SHARE', [location.id])
            location.invalidate_recordset()
        prior = self._find_idempotent_request(event_type, payload)
        if prior:
            return self._event_result(prior.event_id)
        self._validate_execution_state(event_type)
        now = fields.Datetime.now()
        if event_type == CHECKIN:
            self._validate_checkin_window(now, policy_record)
        gps = self._parse_gps_payload(payload, policy_record, event_type)
        status, distance, reference, policy_values = self._classify_location(gps, location, policy_record, event_type)
        review_policy = self._policy_for_status(status, policy_values)
        if status != 'within_radius' and status != 'not_required' and review_policy == 'block':
            raise UserError(self._location_error(status))
        if gps.get('client_acquired_at'):
            self._validate_client_freshness(gps['client_acquired_at'], now)
        token = self._validate_request_token(payload.get('request_token'))
        digest = hashlib.sha256(json.dumps({
            'event_type': event_type, 'visit_id': self.id, 'user_id': self.env.user.id,
            'latitude': gps.get('latitude'), 'longitude': gps.get('longitude'),
            'accuracy_m': gps.get('accuracy_m'), 'client_acquired_at': gps.get('client_acquired_at'),
        }, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        snapshot = dict(policy_values, company_id=company.id, policy_id=policy_record.id if policy_record else False,
                        policy_missing=not bool(policy_record), location_id=reference.id if reference else False,
                        reference_latitude=reference.latitude if reference else False,
                        reference_longitude=reference.longitude if reference else False,
                        reference_radius_m=reference.geofence_radius_m if reference else False,
                        max_accuracy_m=policy_values['max_accuracy_m'])
        try:
            with self.env.cr.savepoint():
                event = self.env['gt.sales.visit.event'].sudo().create({
                    'visit_id': self.id, 'company_id': company.id, 'actor_user_id': self.env.user.id,
                    'event_type': event_type, 'occurred_at': now,
                    'client_acquired_at': gps.get('client_acquired_at'),
                    'latitude': gps.get('latitude'), 'longitude': gps.get('longitude'),
                    'accuracy_m': gps.get('accuracy_m'), 'reference_location_id': reference.id if reference else False,
                    'reference_latitude': reference.latitude if reference else False,
                    'reference_longitude': reference.longitude if reference else False,
                    'reference_radius_m': reference.geofence_radius_m if reference else False,
                    'distance_m': distance, 'location_status': status, 'policy_snapshot': snapshot,
                    'request_token': token,
                })
                exception = False
                if status not in ('within_radius', 'not_required'):
                    exception = self.env['gt.sales.visit.location.exception'].sudo().create({
                        'visit_id': self.id, 'event_id': event.id, 'company_id': company.id,
                        'user_id': self.user_id.id, 'event_type': event_type,
                        'reason_code': status, 'explanation': (payload.get('explanation') or '').strip(),
                    })
                if event_type == CHECKIN:
                    self._apply_transition({
                        'state': 'checked_in', 'checkin_at': now,
                        'checkin_latitude': gps.get('latitude'), 'checkin_longitude': gps.get('longitude'),
                        'checkin_accuracy_m': gps.get('accuracy_m'), 'checkin_event_id': event.id,
                        'checkin_exception_id': exception.id if exception else False,
                        'checkin_policy_snapshot': snapshot,
                    })
                else:
                    duration = (now - self.checkin_at).total_seconds() / 60.0
                    if duration < 0:
                        raise ValidationError('Checkout time cannot precede check-in time.')
                    self._apply_transition({
                        'state': 'checked_out_pending_outcome', 'checkout_at': now,
                        'checkout_latitude': gps.get('latitude'), 'checkout_longitude': gps.get('longitude'),
                        'checkout_accuracy_m': gps.get('accuracy_m'), 'checkout_event_id': event.id,
                        'checkout_exception_id': exception.id if exception else False,
                        'checkout_policy_snapshot': snapshot, 'actual_duration_minutes': duration,
                    })
                self.env['gt.sales.visit.execution.request'].sudo().create({
                    'company_id': company.id, 'visit_id': self.id, 'user_id': self.env.user.id,
                    'event_type': event_type, 'request_token': token, 'payload_hash': digest, 'event_id': event.id,
                })
                return self._event_result(event)
        except (UniqueViolation, ExclusionViolation) as error:
            if getattr(getattr(error, 'diag', None), 'constraint_name', '') == 'gt_sales_visit_unique_execution_request':
                raise UserError('This request token was used by another execution request.') from error
            if getattr(getattr(error, 'diag', None), 'constraint_name', '') == 'gt_sales_visit_one_active_checkin_per_salesperson':
                raise UserError('Check out of your active visit before checking in to another customer.') from error
            raise

    def _validate_execution_state(self, event_type):
        if self.plan_id.state != 'confirmed':
            raise UserError('Execution requires a confirmed daily plan.')
        if self.plan_id.company_id != self.company_id or self.territory_id.company_id != self.company_id:
            raise ValidationError('The visit, plan, territory and company must match.')
        if event_type == CHECKIN:
            if self.state != 'planned' or self.checkin_at or self.checkout_at:
                raise UserError('Only a planned visit can be checked in, and it can be checked in once.')
        elif self.state != 'checked_in' or not self.checkin_at or self.checkout_at:
            raise UserError('Only an active checked-in visit can be checked out once.')
        self.plan_id._validate_plan_context()
        self._check_plan_owner(self.plan_id)
        if self.reschedule_origin_id or self.state in ('cancelled', 'rescheduled'):
            raise UserError('Cancelled or rescheduled visits cannot be executed.')
        mapping = self.env['gt.sales.territory.customer'].with_context(active_test=False).search([
            ('company_id', '=', self.company_id.id), ('territory_id', '=', self.territory_id.id),
            ('partner_id', '=', self.partner_id.id), ('active', '=', True),
        ], limit=1)
        if not mapping:
            raise UserError('The customer is no longer allocated to this visit territory.')

    def _validate_checkin_window(self, now, policy):
        timezone_name = self.plan_id.timezone
        try:
            tz = pytz.timezone(timezone_name)
        except pytz.UnknownTimeZoneError as error:
            raise ValidationError('The visit timezone is invalid.') from error
        local_now = pytz.UTC.localize(now).astimezone(tz)
        plan_date = fields.Date.to_date(self.plan_id.visit_date)
        early = policy.checkin_early_tolerance_min if policy else 0
        late = policy.checkin_late_tolerance_min if policy else 0
        local_start = tz.localize(datetime.combine(plan_date, datetime.min.time())) - timedelta(minutes=early)
        local_end = tz.localize(datetime.combine(plan_date + timedelta(days=1), datetime.min.time())) + timedelta(minutes=late)
        if not local_start <= local_now <= local_end:
            raise UserError('Check-in is outside the permitted local visit date and tolerance window.')

    def _parse_gps_payload(self, payload, policy, event_type):
        has_coordinates = any(key in payload for key in ('latitude', 'longitude', 'accuracy_m'))
        if not has_coordinates:
            return {'latitude': False, 'longitude': False, 'accuracy_m': False,
                    'client_acquired_at': self._parse_client_time(payload.get('client_acquired_at'))}
        values = []
        for key in ('latitude', 'longitude', 'accuracy_m'):
            value = payload.get(key)
            if isinstance(value, bool):
                raise ValidationError('GPS coordinates and accuracy must be numeric.')
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValidationError('GPS coordinates and accuracy must be numeric.')
            if not math.isfinite(number):
                raise ValidationError('GPS coordinates and accuracy must be finite numbers.')
            values.append(number)
        lat, lon, accuracy = values
        if not -90 <= lat <= 90 or not -180 <= lon <= 180 or accuracy < 0:
            raise ValidationError('GPS coordinates or accuracy are outside valid ranges.')
        return {'latitude': lat, 'longitude': lon, 'accuracy_m': accuracy,
                'client_acquired_at': self._parse_client_time(payload.get('client_acquired_at'))}

    @staticmethod
    def _parse_client_time(value):
        if not value:
            return False
        if not isinstance(value, str):
            raise ValidationError('GPS acquisition time must use ISO 8601 format.')
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as error:
            raise ValidationError('GPS acquisition time must use ISO 8601 format.') from error
        if parsed.tzinfo is None:
            raise ValidationError('GPS acquisition time must include a timezone.')
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _validate_client_freshness(client_time, now):
        age = (now - client_time).total_seconds()
        if age < -5 or age > 180:
            raise UserError('The GPS reading is stale. Capture a fresh location and retry.')

    def _classify_location(self, gps, location, policy, event_type):
        company = self.company_id
        values = {
            'require_checkin_gps': policy.require_checkin_gps if policy else True,
            'require_checkout_gps': policy.require_checkout_gps if policy else True,
            'default_radius_m': policy.default_radius_m if policy else 100.0,
            'max_radius_m': policy.max_radius_m if policy else 5000.0,
            'max_accuracy_m': policy.max_accuracy_m if policy else 50.0,
            'missing_policy': policy.missing_policy if policy else 'block',
            'outside_policy': policy.outside_policy if policy else 'block',
            'low_accuracy_policy': policy.low_accuracy_policy if policy else 'block',
            'uncertain_policy': policy.uncertain_policy if policy else 'block',
            'not_configured_policy': policy.not_configured_policy if policy else 'block',
            'policy_exists': bool(policy),
        }
        gps_required = values['require_checkin_gps'] if event_type == CHECKIN else values['require_checkout_gps']
        if gps['latitude'] is False and gps['longitude'] is False and gps['accuracy_m'] is False:
            return ('missing', False, location, values) if gps_required else ('not_required', False, location, values)
        if not location:
            return 'not_configured', False, False, values
        if gps['accuracy_m'] > values['max_accuracy_m']:
            return 'low_accuracy', False, location, values
        distance = self._haversine_m(gps['latitude'], gps['longitude'], location.latitude, location.longitude)
        radius = location.geofence_radius_m or values['default_radius_m']
        if radius > values['max_radius_m']:
            raise ValidationError('The customer geofence radius exceeds the company policy maximum.')
        if distance > radius:
            status = 'outside_radius'
        elif distance + gps['accuracy_m'] <= radius:
            status = 'within_radius'
        else:
            status = 'uncertain'
        values['effective_radius_m'] = radius
        return status, distance, location, values

    @staticmethod
    def _haversine_m(lat1, lon1, lat2, lon2):
        earth_radius = 6371008.8
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        d_phi = math.radians(lat2 - lat1)
        d_lambda = math.radians(lon2 - lon1)
        value = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
        return 2 * earth_radius * math.asin(min(1.0, math.sqrt(value)))

    @staticmethod
    def _policy_for_status(status, values):
        return {
            'missing': values['missing_policy'], 'outside_radius': values['outside_policy'],
            'low_accuracy': values['low_accuracy_policy'], 'uncertain': values['uncertain_policy'],
            'not_configured': values['not_configured_policy'], 'invalid': 'block',
        }.get(status, 'block')

    @staticmethod
    def _location_error(status):
        return {
            'missing': 'GPS location is required for this visit.',
            'outside_radius': 'The reported location is outside the configured customer radius.',
            'low_accuracy': 'GPS accuracy is not sufficient for this visit.',
            'uncertain': 'GPS uncertainty crosses the geofence boundary.',
            'not_configured': 'No approved customer visit location is configured.',
        }.get(status, 'The GPS location could not be verified.')

    def _validate_request_token(self, token):
        try:
            token = str(uuid.UUID(str(token)))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValidationError('A valid unique execution request token is required.') from error
        return token

    def _find_idempotent_request(self, event_type, payload):
        token = self._validate_request_token(payload.get('request_token'))
        request = self.env['gt.sales.visit.execution.request'].sudo().search([
            ('company_id', '=', self.company_id.id), ('request_token', '=', token),
        ], limit=1)
        if not request:
            return False
        if request.user_id != self.env.user or request.visit_id != self or request.event_type != event_type:
            raise AccessError('This request token belongs to another execution request.')
        parsed = self._parse_gps_payload(payload, False, event_type)
        digest = hashlib.sha256(json.dumps({
            'event_type': event_type, 'visit_id': self.id, 'user_id': self.env.user.id,
            'latitude': parsed.get('latitude'), 'longitude': parsed.get('longitude'),
            'accuracy_m': parsed.get('accuracy_m'), 'client_acquired_at': parsed.get('client_acquired_at'),
        }, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if request.payload_hash != digest:
            raise ValidationError('This request token was replayed with different GPS data.')
        return request

    def _event_result(self, event):
        return {'event_id': event.id, 'visit_id': self.id, 'event_type': event.event_type,
                'occurred_at': fields.Datetime.to_string(event.occurred_at),
                'location_status': event.location_status, 'distance_m': event.distance_m,
                'state': self.state, 'duration_minutes': self.actual_duration_minutes or False}

    def action_recover_checkin(self, reason):
        self.ensure_one()
        self.check_access('write')
        check_allowed_company(self.plan_id)
        if not self.env.user.has_group('gt_field_sales_visit_planner.group_sales_manager'):
            raise AccessError('Only a Sales Manager can recover a stranded check-in.')
        if not reason or not reason.strip():
            raise ValidationError('A recovery reason is required.')
        self._lock_for_planning_transition()
        if self.state != 'checked_in' or not self.checkin_at:
            raise UserError('Only an active check-in can be recovered.')
        now = fields.Datetime.now()
        if now < self.checkin_at:
            raise ValidationError('Recovery time cannot precede check-in.')
        event = self.env['gt.sales.visit.event'].sudo().create({
            'visit_id': self.id, 'company_id': self.company_id.id, 'actor_user_id': self.env.user.id,
            'event_type': 'manager_recovery', 'occurred_at': now, 'location_status': 'missing',
            'policy_snapshot': {'recovery': True}, 'explanation': reason.strip(),
        })
        self._apply_transition({'state': 'checked_out_pending_outcome', 'checkout_at': now,
                                'checkout_event_id': event.id,
                                'actual_duration_minutes': (now - self.checkin_at).total_seconds() / 60})
        return True


class VisitExceptionReviewWizard(models.TransientModel):
    _name = 'gt.sales.visit.exception.review.wizard'
    _description = 'Review Visit Location Exception'

    exception_id = fields.Many2one('gt.sales.visit.location.exception', required=True, readonly=True)
    decision = fields.Selection([('approved', 'Approve Exception'), ('rejected', 'Reject Exception')], required=True)
    reason = fields.Text(required=True)

    def action_apply(self):
        self.ensure_one()
        self.exception_id.action_review(self.decision, self.reason)
        return {'type': 'ir.actions.act_window_close'}
