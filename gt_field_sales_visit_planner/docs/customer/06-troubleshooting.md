# Troubleshooting

## Location permission denied or unavailable

Check browser site permissions, device location settings, secure HTTPS context, and whether the browser supports geolocation. Retry only after correcting the issue. A missing observation may require policy review.

## Customer reference location missing

Ask an authorized manager to create and approve the correct company-specific customer visit location. A missing reference cannot be treated as a successful proximity check.

## Territory/customer is not selectable

Ask the manager to check the active company, salesperson assignment dates, territory assignment, and company-specific customer allocation.

## Check-in conflict

Review the salesperson's active visit. The application does not silently check out an earlier visit. Ask an authorized manager to use the explicit recovery process if a visit is stranded.

## Follow-up is not visible

Check the activity assignee, due date, source visit, company access, and standard Odoo activity permissions. A deleted activity is not necessarily completed.

## Dashboard or report access denied

Verify the user's Field Sales role, allowed companies, and authorized territory/team scope. Changing a filter cannot grant access to restricted records.

## XLSX export unavailable

Confirm `XlsxWriter` is installed in the active Odoo Python environment and review server logs for the export error. CSV remains available where authorized.

Support: patelyash.gokul@gmail.com
