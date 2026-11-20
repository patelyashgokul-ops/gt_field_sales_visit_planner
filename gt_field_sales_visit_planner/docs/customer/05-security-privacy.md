# Security and Privacy Notes

- Access is controlled by Odoo groups, record rules, company access, and business-specific server validation.
- Company scope still applies to managers and application administrators; role membership alone does not authorize other companies.
- Precise employee/device location is collected only after a user initiates check-in or checkout. There is no background tracking.
- Browser coordinates are device-reported and can be spoofed. Geofence classification is a policy calculation, not cryptographic or guaranteed proof of physical presence.
- Exception approval records a manager decision; it does not convert the observation into a within-radius result.
- Internal meeting notes and GPS evidence should be limited to authorized users. Routine report exports omit precise coordinates.
- The customer organization is responsible for notices, consent or other lawful basis where applicable, access governance, and retention decisions. This addon does not claim compliance with every jurisdiction and does not automatically purge protected audit history.
- Application-level controls do not protect records from a database superuser or infrastructure administrator with direct database/filestore access.
