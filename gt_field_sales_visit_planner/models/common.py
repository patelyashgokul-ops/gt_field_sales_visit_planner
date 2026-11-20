from odoo.exceptions import AccessError, ValidationError


def check_allowed_company(record):
    """Validate IDs supplied by ORM, RPC, imports, and editable related fields."""
    if not record.company_id or record.company_id not in record.env.companies:
        raise AccessError('You cannot manage field sales data for this company.')


def check_territory_company(record):
    if record.company_id != record.territory_id.company_id:
        raise ValidationError('The company must match the territory company.')
    check_allowed_company(record)
