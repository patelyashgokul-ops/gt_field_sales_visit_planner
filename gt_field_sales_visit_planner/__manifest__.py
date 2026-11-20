{
    'name': 'Field Sales Visit Planner',
    'version': '20.0.1.0.0',
    'category': 'Sales/CRM',
    'summary': 'Company scoped field sales territories and customer allocations',
    'author': 'Golok Technology',
    'license': 'OPL-1',
    'depends': ['base', 'contacts', 'mail'],
    'data': [
        'security/security.xml',
        'security/ir.model.access.csv',
        'views/territory_views.xml',
    ],
    'installable': True,
    'application': True,
}
