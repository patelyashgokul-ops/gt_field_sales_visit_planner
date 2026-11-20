# P07 Security Threat Model and Audit Notes

## Scope and trust boundaries

Protected data includes company-scoped territories, assignments and customer allocations; daily plans and visits; meeting notes and CRM links; location policies, customer reference coordinates, GPS observations and review decisions; execution and business-result events; linked `mail.activity` obligations; report aggregates and exports; and attachments linked to visits. Threat actors include ordinary internal users, salespeople, managers without authority for a target company/team, application administrators outside their allowed companies, portal/public users, and authenticated users crafting ORM, RPC, import, concurrent, or export requests.

Trust boundaries are the browser to authenticated Odoo RPC/controller, public model methods to ORM validation, ORM to PostgreSQL constraints/locks, each allowed company boundary, application visit records to standard CRM/activity/attachment records, report services to source models, and client-provided GPS to server-side classification. GPS coordinates remain client-supplied evidence; distance and status are server-derived, but spoofing cannot be cryptographically ruled out. Database superusers and infrastructure operators are outside application-level immutability guarantees.

## Authorization model

| Data/model family | Salesperson | Manager | Field Sales Admin | Portal/public |
|---|---|---|---|---|
| Territory, assignment, allocation, outcome and policy configuration | Read authorized scope; no configuration | Manage allowed companies | Manage allowed companies | Denied |
| Plans and visits | Own authorized visits; controlled business actions | Authorized company/team scope | Allowed company scope | Denied |
| GPS events and exceptions | Own/authorized evidence, no review of own exception | Review permitted company evidence, no self-approval | Company-bound access; no evidence rewrite | Denied |
| Customer reference locations | Read as needed; no modification | Manage allowed companies | Manage allowed companies | Denied |
| Follow-up activities | Own authorized obligations; standard activity lifecycle | Authorized team scope | Company-bound scope | Denied |
| Reporting and exports | Own authorized scope | Authorized management scope | Allowed company scope | Denied |
| Wizards | Only through their server-validated target operation | Same plus manager-only actions | Company-bound configuration actions | Denied |
| Immutable events | Read where record rules allow; no write/unlink | Same; corrections append events | Same; no rewrite | Denied |

This matrix is a policy summary, not a substitute for Odoo's effective group composition, ACLs, record rules, and standard CRM/activity permissions. `allowed_company_ids` is only accepted after Odoo environment company validation; application role membership alone does not grant access to an otherwise unauthorized company.

## Entry points and controls audited

- Model CRUD overrides and protected business methods in `models/`; visit transitions use explicit actions and revalidate state/company/ownership. SQL locking is parameterized and acquired in the documented order user → plan → visit for planning/execution operations. Database unique/exclusion indexes protect relevant uniqueness invariants.
- The sole custom HTTP route is the authenticated read-only export route. It selects from a fixed report allowlist, validates filters through the reporting service, and returns generated CSV/XLSX data; it does not accept an arbitrary model or ORM domain.
- Reporting uses ORM access checks and validated company/date/territory/user filters. The two aggregate SQL paths use parameterized filters and explicit company joins; sensitive GPS coordinates and internal notes are omitted from ordinary reports.
- Spreadsheet exports serialize untrusted text as text and neutralize formula-like prefixes; XLSX is generated without macros or formula-writing APIs. Actual generated workbook testing is still required before claiming the export is secure.
- Frontend uses escaped QWeb text output, fixed report identifiers, authenticated Odoo services, explicit user-triggered geolocation and server-confirmed state. Request sequence guards prevent stale dashboard results after filter/company changes or component teardown. There is no continuous tracking or intentional coordinate logging.
- There is no addon-specific attachment model/controller. Visit-linked attachments rely on standard Odoo attachment access following the linked source record; P07 adds a negative cross-user attachment read test. Standard administrator/database-level access remains outside this boundary.

## P07 remediation

An audit found that linked follow-up deletion previously relied on a user-controlled context flag. A caller could supply that flag over RPC and delete a source-linked obligation. Deletion now requires the narrowly scoped elevated completion path; `action_feedback` authorizes source visits, appends completion evidence, and then invokes standard activity completion. Direct writes cannot forge the protected source-visit link. Regression tests cover forged context, standard activity completion, and source-link write attempts.

Planning race review found that generic plan cancellation did not lock/revalidate child visit execution state. Cancellation now locks/revalidates plans and visits, rejects plans with executed/missed history, and visit creation/plan transitions use shared locking. Business-result edits and product-interest mutations lock source visits. Territory assignment and customer allocation are rechecked under share locks during plan/visit eligibility validation. These changes are covered by functional tests; real concurrent RPC validation is recorded separately and must not be inferred from sequential or direct-SQL constraint tests.

## Privacy and limitations

Location is collected only after an explicit check-in/out action. No background tracking is implemented. Browser GPS can be spoofed. Application-level append-only rules do not protect against database superusers or direct infrastructure access. Customers must provide applicable worker notices/lawful basis and define their own retention policy; this addon does not claim universal legal compliance or automatically purge business audit history.

## Verification discipline

Use `.test/p07_security_regression.log` for the current actual Odoo upgrade/test run and `.test/p07_security_audit.md` for the final evidence/status ledger. A source-level inspection or test definition is not a passing result. Real browser, concurrent independent-transaction, export-file, and performance checks must be explicitly marked unverified unless actually executed in the available environment.
