/** @odoo-module **/

import { Component, onWillDestroy, onWillStart, proxy } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const REPORTS = {
    territories: { label: "Territory Coverage", columns: [["territory", "Territory"], ["allocated_customers", "Allocated"], ["customers_visited", "Visited"], ["customers_not_visited", "Not Visited"], ["coverage_percent", "Coverage %"], ["completed_visits", "Completed"], ["repeat_visits", "Repeat Visits"]] },
    customers: { label: "Customer Visit Frequency", columns: [["customer", "Customer"], ["territory", "Territory"], ["completed_visits", "Completed Visits"], ["first_completed_at", "First Completed"], ["last_completed_at", "Last Completed"]] },
    salespeople: { label: "Salesperson Performance", columns: [["salesperson", "Salesperson"], ["territory", "Territory"], ["planned", "Eligible Planned"], ["completed", "Completed"], ["missed", "Missed"], ["unexecuted", "Unexecuted"], ["completion_rate", "Completion %"]] },
    followups: { label: "Follow-up Monitoring", columns: [["customer", "Customer"], ["salesperson", "Visit Owner"], ["followup_owner", "Follow-up Owner"], ["summary", "Follow-up"], ["due_date", "Due Date"], ["status", "Status"], ["days_overdue", "Days Overdue"]] },
    exceptions: { label: "GPS Exceptions", columns: [["customer", "Customer"], ["salesperson", "Salesperson"], ["territory", "Territory"], ["event", "Event"], ["classification", "Location Assessment"], ["review_state", "Review Status"], ["reason", "Reason"], ["reviewed_at", "Reviewed At"]] },
    outcomes: { label: "Visit Outcomes", columns: [["outcome", "Outcome"], ["visits", "Completed Visits"]] },
};

export class FieldSalesDashboard extends Component {
    static template = "gt_field_sales_visit_planner.Dashboard";
    get state() { return this._state; }
    set state(value) { this._state = value; }

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = proxy({ loading: true, error: "", data: null, report: "overview", rows: [], reportLoading: false,
            filters: { company_id: "", date_from: "", date_to: "", territory_id: "", user_id: "" } });
        this._dashboardRequest = 0;
        this._reportRequest = 0;
        this._destroyed = false;
        onWillDestroy(() => {
            this._destroyed = true;
            this._dashboardRequest++;
            this._reportRequest++;
        });
        onWillStart(() => this.reload());
    }

    get reportOptions() { return REPORTS; }
    get reportColumns() { return REPORTS[this.state.report]?.columns || []; }
    get maxTrend() { return Math.max(1, ...(this.state.data?.trends || []).map((x) => x.planned)); }

    async reload() {
        const requestId = ++this._dashboardRequest;
        // Invalidate a report loaded under the previous filter/company context.
        this._reportRequest++;
        this.state.loading = true;
        this.state.error = "";
        const filters = this.cleanFilters();
        try {
            const data = await this.orm.call("gt.sales.reporting", "get_dashboard_data", [filters]);
            if (this._destroyed || requestId !== this._dashboardRequest) return;
            this.state.data = data;
            this.state.filters.company_id = String(data.filters.company_id);
            this.state.filters.date_from = data.filters.date_from;
            this.state.filters.date_to = data.filters.date_to;
            this.state.filters.territory_id = data.filters.territory_id ? String(data.filters.territory_id) : "";
            this.state.filters.user_id = data.filters.user_id ? String(data.filters.user_id) : "";
            this.state.loading = false;
            if (this.state.report !== "overview") await this.loadReport();
        } catch (error) {
            if (this._destroyed || requestId !== this._dashboardRequest) return;
            this.state.loading = false;
            this.state.error = error.message || "Unable to load Field Sales analytics.";
        }
    }

    cleanFilters() {
        const filters = {};
        for (const key of ["company_id", "territory_id", "user_id", "date_from", "date_to"]) {
            const value = this.state.filters[key];
            if (value !== "" && value !== null && value !== undefined) filters[key] = value;
        }
        return filters;
    }

    async onFilterChange() {
        if (this.state.filters.company_id !== String(this.state.data?.filters.company_id)) {
            this.state.filters.territory_id = "";
            this.state.filters.user_id = "";
        }
        await this.reload();
    }

    onCompanyChange(ev) { this.state.filters.company_id = ev.target.value; return this.onFilterChange(); }
    onDateFromChange(ev) { this.state.filters.date_from = ev.target.value; return this.onFilterChange(); }
    onDateToChange(ev) { this.state.filters.date_to = ev.target.value; return this.onFilterChange(); }
    onTerritoryChange(ev) { this.state.filters.territory_id = ev.target.value; return this.onFilterChange(); }
    onSalespersonChange(ev) { this.state.filters.user_id = ev.target.value; return this.onFilterChange(); }

    async selectReport(report) {
        this.state.report = report;
        if (report !== "overview") await this.loadReport();
    }

    async loadReport() {
        const requestId = ++this._reportRequest;
        const report = this.state.report;
        const filters = this.cleanFilters();
        this.state.reportLoading = true;
        try {
            const rows = await this.orm.call("gt.sales.reporting", "get_report_rows", [report, filters, 100]);
            if (this._destroyed || requestId !== this._reportRequest) return;
            this.state.rows = rows;
        } catch (error) {
            if (this._destroyed || requestId !== this._reportRequest) return;
            this.state.rows = [];
            this.notification.add(error.message || "Unable to load report.", { type: "danger" });
        } finally {
            if (!this._destroyed && requestId === this._reportRequest) this.state.reportLoading = false;
        }
    }

    async openMetric(metric) {
        try {
            const action = await this.orm.call("gt.sales.reporting", "get_drilldown_action", [metric, this.cleanFilters()]);
            await this.action.doAction(action);
        } catch (error) {
            this.notification.add(error.message || "You cannot open those records.", { type: "danger" });
        }
    }

    exportUrl(extension) {
        const params = new URLSearchParams({ filters: JSON.stringify(this.cleanFilters()) });
        return `/gt_field_sales_visit_planner/export/${this.state.report}.${extension}?${params}`;
    }

    labelStatus(status) {
        return { planned: "Planned", checked_in: "Checked In", pending_outcome: "Checked Out — Pending Outcome",
            completed: "Completed", missed: "Missed", cancelled: "Cancelled", rescheduled: "Rescheduled" }[status] || status;
    }
}

registry.category("actions").add("gt_field_sales_visit_planner.dashboard", FieldSalesDashboard);
