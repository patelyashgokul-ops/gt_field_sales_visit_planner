# Field Sales Reporting Metric Contract (P05)

## Date cohort

Visit planning, completion, missed, unexecuted, status, customer-frequency, territory-coverage, outcome, and GPS-exception reports use `gt.sales.visit.planned_date`, which is the plan's scheduled local calendar date. The UI labels the selected range as scheduled visit dates. This keeps planned-versus-completed rates on a consistent visit cohort. Timestamped execution evidence remains UTC and is displayed in the selected company's reporting timezone.

The selected company timezone is `res.company.partner_id.tz`, then the current user's timezone, then UTC. The reporting service validates the timezone. Follow-up overdue status uses the selected company's local reporting date and the source-linked standard Odoo activity's due date.

## Visit definitions

- Eligible planned cohort: records in confirmed plans with state `planned`, `checked_in`, `checked_out_pending_outcome`, `completed`, or `missed`. Cancelled and rescheduled source records are excluded. Draft plans are excluded.
- Completed: `completed` state with server completion time and recorded check-in and checkout evidence links.
- Completion rate: completed / eligible planned cohort. A zero denominator is `No eligible planned visits` and the percentage is blank.
- Missed: only explicit `missed` state.
- Unexecuted: confirmed-plan visits still `planned`; overdue is a separate count where scheduled date is before the selected company's reporting date.
- Checked-in and checked-out-pending-outcome are separate from completed.
- Cancelled and rescheduled records are reported as historical counts and excluded from current eligible planned denominator.

## Territory coverage

Coverage is **Current Allocation Coverage** because P01 allocations do not have effective-from/effective-to dates. Denominator is distinct active currently allocated customers in an active territory/company. Numerator is distinct such customers with a completed, evidence-linked visit in the selected scheduled-date cohort. Missed, cancelled, rescheduled, and pending-outcome visits do not establish coverage. Repeats are completed visit count minus distinct customers visited. Location exception state does not remove business-completed visits; GPS assessments remain separate.

## Follow-ups

Follow-up metrics and report rows use the selected scheduled-visit cohort through the source visit, not the activity due-date range. Open means active source-linked activity. Due today and overdue compare `date_deadline` to selected-company local reporting date. Completed requires P04's server evidence (`followup_completed_at`). An archived/deleted activity without that evidence is not counted completed. This allows overdue obligations due before the selected range to remain visible when their source visits belong to the selected cohort.

## Location and outcome

GPS classifications come from the immutable P03 event/exception evidence. Review states are reported independently. Approved exceptions are not labeled verified GPS. Outcome distribution groups immutable P04 `outcome_snapshot` code and name, not current mutable master labels.

## Access and exports

Reporting APIs authorize the Field Sales application role before aggregation, validate one company against the user's allowed companies, and validate territory and salesperson filters. ORM queries retain record rules. The two narrowly scoped SQL aggregations (coverage join and outcome-snapshot grouping) use parameterized statements and repeat the exact allowed company, selected user/territory, state, and date restrictions after authorization. Summary reports exclude precise GPS coordinates, private meeting notes, and CRM descriptions. Exports are capped at 10,000 rows per request.
