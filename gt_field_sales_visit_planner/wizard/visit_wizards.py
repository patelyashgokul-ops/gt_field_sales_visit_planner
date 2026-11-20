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
