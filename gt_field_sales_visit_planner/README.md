# Field Sales Visit Planner (P01 foundation)

Publisher: Golok Technology  
Support: patelyash.gokul@gmail.com  
License: OPL-1

This Odoo 20 Community addon provides company scoped sales territories,
salesperson assignments, and customer territory allocations. It does not yet
provide visit execution workflows.

## Setup

Grant one of the Field Sales Access roles to each internal user. Sales Managers
and Field Sales Administrators can configure records in their active allowed
companies. Field Salespersons can read their own assignments and the associated
territories and allocations. Use the company switcher when managing more than
one authorized company.

To move a customer to another territory, archive its current allocation and
create a new one. The prior allocation stays in the database, and changes are
tracked in the allocation chatter. Assignments also remain as historical
records when archived. Territories with dependent records can be archived but
cannot be deleted or moved to another company.

## Development

Discoverable Odoo tests live in `tests/`. Local test configuration and logs
live in `.test/`. The module depends only on Community addons `base`,
`contacts`, and `mail`.
