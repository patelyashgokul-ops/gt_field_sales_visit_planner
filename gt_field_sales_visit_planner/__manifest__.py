{
    'name': 'Field Sales Visit Planner',
    'version': '20.0.1.0.0',
    'category': 'Sales/CRM',
    'summary': 'Plan and manage territory-based customer visits',
    'author': 'Golok Technology',
    'license': 'OPL-1',
    'depends': ['base', 'contacts', 'mail', 'crm'],
    'pre_init_hook': 'pre_init_hook',
    'data': [
        'security/security.xml',
        'security/ir.access.csv',
        'views/territory_views.xml',
        'views/planning_views.xml',
        'wizard/reschedule_visit_views.xml',
    ],
    'installable': True,
    'application': True,
}
