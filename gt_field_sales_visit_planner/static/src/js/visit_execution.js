/** @odoo-module **/

import { registry } from "@web/core/registry";
import { ConnectionLostError } from "@web/core/network/rpc";
import { useService } from "@web/core/utils/hooks";
import { Component, signal, useProps } from "@odoo/owl";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

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
    props = useProps(standardWidgetProps);
    pending = signal(false);
    error = signal("");
    captureError = signal("");
    resultMessage = signal("");
    retryPayload = signal(null);

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
    }

    get visitState() {
        return this.props.record.data.state;
    }

    async onExecute(eventType) {
        if (this.pending()) {
            return;
        }
        this.error.set("");
        this.captureError.set("");
        this.resultMessage.set("");
        if (!window.isSecureContext) {
            this.captureError.set("Location capture requires a secure browser context (HTTPS or localhost).");
            return;
        }
        if (!navigator.geolocation) {
            this.captureError.set("This browser does not support location capture.");
            return;
        }
        this.pending.set(true);
        navigator.geolocation.getCurrentPosition(
            (position) => this._submit(eventType, {
                latitude: position.coords.latitude,
                longitude: position.coords.longitude,
                accuracy_m: position.coords.accuracy,
                client_acquired_at: new Date(position.timestamp).toISOString(),
                request_token: this._newRequestToken(),
            }),
            (error) => {
                this.pending.set(false);
                const messages = {
                    1: "Location permission was denied.",
                    2: "The device could not determine its location.",
                    3: "Location capture timed out.",
                };
                this.captureError.set(messages[error.code] || "Location capture failed.");
            },
            { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
        );
    }

    async onContinueWithoutLocation(eventType) {
        if (!this.pending()) {
            this.pending.set(true);
            await this._submit(eventType, {
                request_token: this._newRequestToken(),
                explanation: this.captureError() || "The browser did not provide a location.",
            });
        }
    }

    async onRetry() {
        if (this.pending() || !this.retryPayload()) {
            return;
        }
        const { eventType, payload } = this.retryPayload();
        this.pending.set(true);
        await this._submit(eventType, payload, true);
    }

    _newRequestToken() {
        if (globalThis.crypto?.randomUUID) {
            return globalThis.crypto.randomUUID();
        }
        if (!globalThis.crypto?.getRandomValues) {
            throw new Error("Secure request identifiers are unavailable in this browser.");
        }
        const bytes = new Uint8Array(16);
        globalThis.crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }

    async _submit(eventType, payload, retry = false) {
        const method = eventType === "check_in" ? "action_check_in" : "action_check_out";
        const visitId = this.props.record.resId;
        const requestPayload = retry ? payload : { ...payload };
        if (!retry) {
            this.retryPayload.set({ eventType, payload: requestPayload });
        }
        try {
            const result = await this.orm.call("gt.sales.visit", method, [[visitId], requestPayload]);
            await this.props.record.load();
            this.retryPayload.set(null);
            this.captureError.set("");
            const status = STATUS_LABELS[result.location_status] || "Recorded";
            const operation = eventType === "check_out"
                ? "Checked out. Meeting outcome pending."
                : "Check-in recorded.";
            const resultMessage = `${operation} Location assessment: ${status}.`;
            this.resultMessage.set(resultMessage);
            this.notification.add(resultMessage, {
                type: ["within_radius", "not_required"].includes(result.location_status) ? "success" : "warning",
            });
        } catch (error) {
            this.error.set(error?.data?.message || error.message || "The visit operation failed.");
            if (!(error instanceof ConnectionLostError)) {
                this.retryPayload.set(null);
            }
            this.notification.add(this.error(), { type: "danger" });
        } finally {
            this.pending.set(false);
        }
    }
}

registry.category("view_widgets").add("gt_visit_execution", { component: VisitExecutionWidget });
