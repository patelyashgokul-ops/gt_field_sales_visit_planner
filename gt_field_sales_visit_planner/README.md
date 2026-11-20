# Field Sales Visit Planner

Publisher: Golok Technology  
Support: patelyash.gokul@gmail.com  
License: OPL-1

This Odoo 20 Community addon provides company-scoped territory configuration,
salesperson assignments, customer allocations, daily visit planning, GPS-assisted
visit execution, structured meeting outcomes, CRM linkage, Odoo follow-up
activities, and management reporting.

## Setup

Install `base`, `contacts`, `mail`, and `crm`, and install the Python package
`XlsxWriter` in the same environment as Odoo for XLSX exports. Assign a Field
Sales role to each internal user and restrict allowed companies to the user's
actual responsibilities. See `docs/customer/` for installation, administrator,
salesperson, manager, privacy, troubleshooting, and release guidance.

Browser GPS is collected only after a user initiates a visit action. Device
coordinates are not cryptographic proof of presence. An approved location
exception does not mean GPS was within the configured radius. Customers are
responsible for employee notices, lawful basis where applicable, and retention
policies.

The module declares OPL-1. Odoo Apps vendor guidance lists OPL-1 as compatible
with OSI-approved licenses; XlsxWriter is BSD-2-Clause. The dependency is not
bundled. Review current upstream terms and notices before redistribution.

## Development

Discoverable Odoo tests live in `tests/`. Local test configuration, QA evidence,
and release artifacts live in `.test/`. Do not ship `.test/` in the customer ZIP.
