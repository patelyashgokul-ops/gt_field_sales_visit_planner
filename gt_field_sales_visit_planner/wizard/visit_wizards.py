from odoo import fields, models
from odoo.exceptions import ValidationError


class VisitRescheduleWizard(models.TransientModel):
    _name = 'gt.sales.visit.reschedule.wizard'
    _description = 'Reschedule Customer Visit'
    visit_id = fields.Many2one('gt.sales.visit', required=True)
    target_date = fields.Date(required=True)
    target_plan_id = fields.Many2one('gt.sales.visit.plan')
    reason = fields.Text(required=True)

    def action_apply(self):
        self.ensure_one()
        if not self.reason or not self.reason.strip():
            raise ValidationError('A reschedule reason is required.')
        self.visit_id.action_reschedule(self.target_date, self.reason, self.target_plan_id)
        return {'type': 'ir.actions.act_window_close'}


class PlanCancelWizard(models.TransientModel):
    _name = 'gt.sales.plan.cancel.wizard'
    _description = 'Cancel Visit Plan'
    plan_id = fields.Many2one('gt.sales.visit.plan', required=True)
    reason = fields.Text(required=True)

    def action_apply(self):
        self.ensure_one()
        self.plan_id.action_cancel(self.reason)
        return {'type': 'ir.actions.act_window_close'}


class PlanAmendWizard(models.TransientModel):
    _name = 'gt.sales.plan.amend.wizard'
    _description = 'Amend Confirmed Visit Plan'
    plan_id = fields.Many2one('gt.sales.visit.plan', required=True)
    reason = fields.Text(required=True)
    notes = fields.Text()
    ordered_visit_ids = fields.Char(help='Optional comma-separated visit record IDs in the desired route order.')

    def action_apply(self):
        self.ensure_one()
        ids = None
        if self.ordered_visit_ids:
            try:
                ids = [int(value.strip()) for value in self.ordered_visit_ids.split(',') if value.strip()]
            except ValueError as error:
                raise ValidationError('Visit order must contain numeric visit IDs separated by commas.') from error
        self.plan_id.action_amend(self.reason, ordered_visit_ids=ids, notes=self.notes)
        return {'type': 'ir.actions.act_window_close'}
