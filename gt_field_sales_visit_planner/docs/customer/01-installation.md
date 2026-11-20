# Installation Guide

## Supported platform

This addon targets Odoo 20.0 Community. It depends on the Community modules `base`, `contacts`, `mail`, and `crm`, plus the Python package `XlsxWriter` for XLSX export. Install the Python dependency in the Odoo server environment before installing this addon. Confirm its license is compatible with your distribution terms before commercial redistribution.

## Install

1. Back up the target database and filestore.
2. Add the extracted `gt_field_sales_visit_planner` folder to an Odoo addons path.
3. Install `XlsxWriter` in the same Python environment used by Odoo.
4. Restart Odoo, update the Apps list, and install **Field Sales Visit Planner**.
5. Assign Field Sales roles to internal users and select their allowed companies.
6. Follow the Administrator Guide before scheduling live visits.

This addon does not provide a native Android/iOS application or offline synchronization. Browser GPS requires a supported browser and a secure context (normally HTTPS, except browser localhost development contexts). Odoo Apps vendor guidance lists BSD-2-Clause as an OSI-approved dependency license compatible with OPL-1; XlsxWriter is not bundled with this addon.

## Common installation issues

- Missing dependency: install the Community dependencies and `XlsxWriter` into Odoo's active Python environment.
- Missing menu: verify the user is internal, has an appropriate Field Sales role, and has the intended company enabled.
- Upgrade errors: test upgrades on a database copy first and retain the database and filestore backup.

Support: [patelyash.gokul@gmail.com](mailto:patelyash.gokul@gmail.com)
