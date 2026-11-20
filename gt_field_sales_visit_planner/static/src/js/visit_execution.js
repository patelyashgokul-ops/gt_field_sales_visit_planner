/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, useState } from "@odoo/owl";

const STATUS_LABELS = {
    within_radius: "Within configured radius",
    outside_radius: "Outside configured radius",
    low_accuracy: "Location review required: low GPS accuracy",
    uncertain: "Location review required: geofence uncertainty",
    missing: "Location review required: GPS unavailable",
    not_configured: "Location review required: no approved customer location",
    not_required: "GPS was not required by company policy",
};

export class VisitExecutionWidget extends Component {
    static template = "gt_field_sales_visit_planner.VisitExecutionWidget";
    static props = { record: Object, readonly: { type: Boolean, optional: true } };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({ pending: false, error: "", captureError: "", retryPayload: null });
    }

    get visitState() {
        return this.props.record.data.state;
    }

    async onExecute(eventType) {
        if (this.state.pending) {
            return;
        }
        this.state.error = "";
        this.state.captureError = "";
        if (!window.isSecureContext) {
            this.state.captureError = "Location capture requires a secure browser context (HTTPS or localhost).";
            return;
        }
        if (!navigator.geolocation) {
            this.state.captureError = "This browser does not support location capture.";
            return;
        }
        this.state.pending = true;
        navigator.geolocation.getCurrentPosition(
            (position) => this._submit(eventType, {
                latitude: position.coords.latitude,
                longitude: position.coords.longitude,
                accuracy_m: position.coords.accuracy,
                client_acquired_at: new Date(position.timestamp).toISOString(),
                request_token: crypto.randomUUID(),
            }),
            (error) => {
                this.state.pending = false;
                const messages = {
                    1: "Location permission was denied.",
                    2: "The device could not determine its location.",
                    3: "Location capture timed out.",
                };
                this.state.captureError = messages[error.code] || "Location capture failed.";
            },
            { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
        );
    }

    async onContinueWithoutLocation(eventType) {
        if (!this.state.pending) {
            this.state.pending = true;
            await this._submit(eventType, {
                request_token: crypto.randomUUID(),
                explanation: this.state.captureError || "The browser did not provide a location.",
            });
        }
    }

    async onRetry() {
        if (this.state.pending || !this.state.retryPayload) {
            return;
        }
        const { eventType, payload } = this.state.retryPayload;
        this.state.pending = true;
        await this._submit(eventType, payload, true);
    }

    async _submit(eventType, payload, retry = false) {
        const method = eventType === "check_in" ? "action_check_in" : "action_check_out";
        const visitId = this.props.record.resId;
        const requestPayload = retry ? payload : { ...payload };
        if (!retry) {
            this.state.retryPayload = { eventType, payload: requestPayload };
        }
        try {
            const result = await this.orm.call("gt.sales.visit", method, [[visitId], requestPayload]);
            await this.props.record.load();
            this.state.retryPayload = null;
            this.state.captureError = "";
            const status = STATUS_LABELS[result.location_status] || "Recorded";
            this.notification.add(`${eventType === "check_in" ? "Check-in" : "Check-out"} recorded. ${status}.`, {
                type: "success",
            });
        } catch (error) {
            this.state.error = error?.data?.message || error.message || "The visit operation failed.";
            this.notification.add(this.state.error, { type: "danger" });
        } finally {
            this.state.pending = false;
        }
    }
}

registry.category("view_widgets").add("gt_visit_execution", { component: VisitExecutionWidget });
