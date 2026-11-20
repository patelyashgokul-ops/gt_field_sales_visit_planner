from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from psycopg2.errors import UniqueViolation

from .common import check_allowed_company


class SalesTerritory(models.Model):
    _name = 'gt.sales.territory'
    _description = 'Sales Territory'
    _order = 'company_id, name'
    _check_company_auto = True

    name = fields.Char(required=True, index=True)
    code = fields.Char(required=True, index=True)
    company_id = fields.Many2one('res.company', required=True, index=True,
                                 default=lambda self: self.env.company, ondelete='restrict')
    active = fields.Boolean(default=True)
    description = fields.Text()
    assignment_ids = fields.One2many('gt.sales.territory.assignment', 'territory_id')
    customer_mapping_ids = fields.One2many('gt.sales.territory.customer', 'territory_id')

    _company_code_unique = models.Constraint('UNIQUE(company_id, code)',
                                               'Territory code must be unique within the company.')
    _name_not_blank = models.Constraint("CHECK(length(btrim(name)) > 0)", 'Territory name cannot be blank.')
    _code_not_blank = models.Constraint("CHECK(length(btrim(code)) > 0)", 'Territory code cannot be blank.')

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access('create')
        for vals in vals_list:
            company = self.env['res.company'].browse(vals.get('company_id') or self.env.company.id)
            if not company.exists() or company not in self.env.companies:
                raise AccessError('You cannot create a territory for this company.')
            for key in ('name', 'code'):
                if not (vals.get(key) or '').strip():
                    raise ValidationError('%s cannot be blank.' % key.capitalize())
                vals[key] = vals[key].strip()
        try:
            with self.env.cr.savepoint():
                return super().create(vals_list)
        except UniqueViolation as error:
            if error.diag.constraint_name != 'gt_sales_territory_company_code_unique':
                raise
            raise ValidationError('Territory code must be unique within the company.') from error

    def write(self, vals):
        for record in self:
            check_allowed_company(record)
            if 'company_id' in vals and vals['company_id'] != record.company_id.id and (
                record.with_context(active_test=False).assignment_ids
                or record.with_context(active_test=False).customer_mapping_ids
            ):
                raise ValidationError('A territory with assignments or allocations cannot change company.')
        if 'company_id' in vals:
            company = self.env['res.company'].browse(vals['company_id'])
            if not company.exists() or company not in self.env.companies:
                raise AccessError('You cannot move a territory to this company.')
        for key in ('name', 'code'):
            if key in vals:
                if not (vals[key] or '').strip():
                    raise ValidationError('%s cannot be blank.' % key.capitalize())
                vals[key] = vals[key].strip()
        try:
            with self.env.cr.savepoint():
                return super().write(vals)
        except UniqueViolation as error:
            if error.diag.constraint_name != 'gt_sales_territory_company_code_unique':
                raise
            raise ValidationError('Territory code must be unique within the company.') from error

    def unlink(self):
        for record in self:
            check_allowed_company(record)
            if (record.with_context(active_test=False).assignment_ids
                    or record.with_context(active_test=False).customer_mapping_ids):
                raise ValidationError('Archive a territory with historical assignments or allocations.')
        return super().unlink()
