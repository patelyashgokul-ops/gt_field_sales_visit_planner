import csv
import io
import json

import xlsxwriter

from odoo import http
from odoo.http import request
from odoo.http.stream import content_disposition


class FieldSalesReportExport(http.Controller):
    @http.route('/gt_field_sales_visit_planner/export/<string:report>.<string:extension>',
                type='http', auth='user', methods=['GET'], csrf=False)
    def export_report(self, report, extension, filters=None, **kwargs):
        if extension not in ('csv', 'xlsx'):
            return request.not_found()
        try:
            decoded_filters = json.loads(filters or '{}')
        except (TypeError, ValueError):
            return request.make_response('Invalid report filters.', status=400)
        payload = request.env['gt.sales.reporting'].get_export_payload(report, decoded_filters)
        filename = payload['filename'] + '.' + extension
        output = io.BytesIO()
        if extension == 'csv':
            text = io.StringIO(newline='')
            writer = csv.writer(text, dialect='excel', lineterminator='\r\n')
            writer.writerow(payload['columns'])
            safe = request.env['gt.sales.reporting'].spreadsheet_safe_text
            for row in payload['rows']:
                writer.writerow([value if isinstance(value, (int, float)) and not isinstance(value, bool)
                                 else safe(value) for value in row])
            output.write(('\ufeff' + text.getvalue()).encode('utf-8'))
            mimetype = 'text/csv; charset=utf-8'
        else:
            workbook = xlsxwriter.Workbook(output, {'in_memory': True})
            sheet = workbook.add_worksheet('Field Sales Report')
            header = workbook.add_format({'bold': True, 'bg_color': '#17365D', 'font_color': '#FFFFFF'})
            meta = workbook.add_format({'italic': True, 'font_color': '#53657A'})
            sheet.write_string(0, 0, f"Company: {payload['company']}", meta)
            sheet.write_string(1, 0, f"Scheduled visit period: {payload['date_from']} to {payload['date_to']}", meta)
            for column, title in enumerate(payload['columns']):
                sheet.write_string(3, column, title, header)
                sheet.set_column(column, column, min(max(len(title) + 3, 14), 34))
            safe = request.env['gt.sales.reporting'].spreadsheet_safe_text
            for row_index, row in enumerate(payload['rows'], start=4):
                for column, value in enumerate(row):
                    if isinstance(value, bool):
                        sheet.write_boolean(row_index, column, value)
                    elif isinstance(value, (int, float)):
                        sheet.write_number(row_index, column, value)
                    else:
                        sheet.write_string(row_index, column, safe(value))
            sheet.freeze_panes(4, 0)
            sheet.autofilter(3, 0, max(3, 3 + len(payload['rows'])), max(0, len(payload['columns']) - 1))
            workbook.close()
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return request.make_response(output.getvalue(), headers=[
            ('Content-Type', mimetype), ('Content-Length', str(len(output.getvalue()))),
            ('Content-Disposition', content_disposition(filename)),
            ('X-Content-Type-Options', 'nosniff'),
        ])
