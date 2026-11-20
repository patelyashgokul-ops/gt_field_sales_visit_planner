from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .common import check_allowed_company, check_territory_company


class SalesTerritoryCustomer(models.Model):
    _name = 'gt.sales.territory.customer'
    _description = 'Customer Territory Allocation'
    _inherit = ['mail.thread']
    _order = 'id desc'
    _check_company_auto = True

    territory_id = fields.Many2one('gt.sales.territory', required=True, index=True,
                                    check_company=True, ondelete='restrict', tracking=True)
    partner_id = fields.Many2one('res.partner', required=True, index=True, ondelete='restrict',
                                  tracking=True, domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    company_id = fields.Many2one('res.company', required=True, index=True, ondelete='restrict', tracking=True)
    active = fields.Boolean(default=True, tracking=True)

    _one_active_customer = models.UniqueIndex(
        '(company_id, partner_id) WHERE active IS TRUE',
        'A customer can have only one active territory allocation per company.',
    )

    def _validate_business(self):
        for record in self:
            check_territory_company(record)
            if not record.partner_id.exists():
                raise ValidationError('Choose an existing customer.')
            record.partner_id.check_access('read')
            if record.partner_id.company_id and record.partner_id.company_id != record.company_id:
                raise ValidationError('The customer belongs to another company.')
            if record.active and not record.territory_id.active:
                raise ValidationError('An archived territory cannot receive active allocations.')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            territory = self.env['gt.sales.territory'].browse(vals.get('territory_id'))
            if not territory.exists() or territory.company_id not in self.env.companies:
                raise AccessError('You cannot allocate a customer to this territory.')
            if vals.get('company_id') != territory.company_id.id:
                raise ValidationError('The company must match the territory company.')
        records = super().create(vals_list)
        records._validate_business()
        return records

    def write(self, vals):
        for record in self:
            check_allowed_company(record)
        result = super().write(vals)
        self._validate_business()
        if vals.get('active') is False:
            # Make the release visible to the partial unique index before a
            # replacement allocation is created in the same transaction.
            self.flush_recordset(['active'])
        return result

    def unlink(self):
        raise AccessError('Allocations are historical records. Archive and create a new allocation instead.')
