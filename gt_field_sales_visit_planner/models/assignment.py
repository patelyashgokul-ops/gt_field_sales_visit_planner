from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .common import check_allowed_company, check_territory_company


class SalesTerritoryAssignment(models.Model):
    _name = 'gt.sales.territory.assignment'
    _description = 'Sales Territory Assignment'
    _order = 'date_from desc, id desc'
    _check_company_auto = True

    territory_id = fields.Many2one('gt.sales.territory', required=True, index=True,
                                    check_company=True, ondelete='restrict')
    user_id = fields.Many2one('res.users', required=True, index=True, ondelete='restrict',
                              domain="[('share', '=', False), ('company_ids', 'in', company_id)]")
    company_id = fields.Many2one('res.company', required=True, index=True, ondelete='restrict')
    date_from = fields.Date(required=True, index=True)
    date_to = fields.Date(index=True)
    active = fields.Boolean(default=True)

    _valid_dates = models.Constraint('CHECK(date_to IS NULL OR date_to >= date_from)',
                                      'The end date must not precede the start date.')

    def _validate_business(self):
        for record in self:
            check_territory_company(record)
            if not record.user_id.exists() or record.user_id.share or not record.user_id.has_group('base.group_user'):
                raise ValidationError('Only internal users can be assigned to territories.')
            if record.company_id not in record.user_id.company_ids:
                raise ValidationError('The salesperson must belong to the territory company.')
            if record.active and not record.territory_id.active:
                raise ValidationError('An archived territory cannot receive active assignments.')
            if record.date_to and record.date_to < record.date_from:
                raise ValidationError('The assignment end date must not precede its start date.')
            if not record.active:
                continue
            peers = self.with_context(active_test=False).search([
                ('id', '!=', record.id), ('user_id', '=', record.user_id.id),
                ('territory_id', '=', record.territory_id.id), ('active', '=', True),
                ('date_from', '<=', record.date_to or '9999-12-31'),
                '|', ('date_to', '=', False), ('date_to', '>=', record.date_from),
            ], limit=1)
            if peers:
                raise ValidationError('This salesperson already has an overlapping active assignment.')

    @api.model_create_multi
    def create(self, vals_list):
        user_ids = [vals.get('user_id') for vals in vals_list if vals.get('user_id')]
        if user_ids:
            self.env.cr.execute('SELECT id FROM res_users WHERE id = ANY(%s) ORDER BY id FOR UPDATE', [user_ids])
        for vals in vals_list:
            territory = self.env['gt.sales.territory'].browse(vals.get('territory_id'))
            if not territory.exists() or territory.company_id not in self.env.companies:
                raise AccessError('You cannot assign a user to this territory.')
            if not territory.active:
                raise ValidationError('An archived territory cannot receive new assignments.')
            if vals.get('company_id') != territory.company_id.id:
                raise ValidationError('The company must match the territory company.')
        records = super().create(vals_list)
        records._validate_business()
        return records

    def write(self, vals):
        for record in self:
            check_allowed_company(record)
        user_ids = sorted(set(self.mapped('user_id').ids + ([vals['user_id']] if vals.get('user_id') else [])))
        if user_ids:
            self.env.cr.execute('SELECT id FROM res_users WHERE id = ANY(%s) ORDER BY id FOR UPDATE', [user_ids])
        result = super().write(vals)
        self._validate_business()
        return result

    def unlink(self):
        raise AccessError('Assignments are historical records. Archive them instead.')
