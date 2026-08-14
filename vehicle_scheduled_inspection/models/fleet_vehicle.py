from dateutil.relativedelta import relativedelta
from datetime import datetime, time
import pytz

from odoo import _, api, fields, models


class FleetVehicle(models.Model):
    _inherit = "fleet.vehicle"

    def _compute_average_daily_km(self):
        self.ensure_one()
        thirty_days_ago = fields.Date.today() - relativedelta(days=30)
        domain = [("vehicle_id", "=", self.id), ("date", ">=", thirty_days_ago)]
        if self.driver_id:
            # Assuming fleet.vehicle.odometer has driver_id in this environment
            # If it throws error, we'll need to remove it, but Odoo 13+ fleet has it or we can fallback
            odometer_model_fields = self.env["fleet.vehicle.odometer"]._fields
            if "driver_id" in odometer_model_fields:
                domain.append(("driver_id", "=", self.driver_id.id))

        logs = self.env["fleet.vehicle.odometer"].search(domain, order="date asc")
        if logs and len(logs) > 1:
            first = logs[0]
            last = logs[-1]
            days = (last.date - first.date).days
            if days > 0:
                return (last.value - first.value) / days
        return 0.0

    @api.model
    def _cron_schedule_inspections(self):
        vehicles = self.search([("active", "=", True)])
        today = fields.Date.today()

        tz = pytz.timezone('America/Sao_Paulo')
        def exact_date_8am(date, days_to_add):
            exact_date = date + relativedelta(days=int(days_to_add))
            
            # Se cair no final de semana, empurra para o próximo dia útil (segunda-feira)
            while exact_date.weekday() >= 5:  # 5: Saturday, 6: Sunday
                exact_date += relativedelta(days=1)
                
            local_dt = tz.localize(datetime.combine(exact_date, time(8, 0)))
            now_local = datetime.now(pytz.utc).astimezone(tz)
            
            if local_dt <= now_local:
                exact_date = now_local.date() + relativedelta(days=1)
                while exact_date.weekday() >= 5:
                    exact_date += relativedelta(days=1)
                local_dt = tz.localize(datetime.combine(exact_date, time(8, 0)))

            return local_dt.astimezone(pytz.utc).replace(tzinfo=None)

        for vehicle in vehicles:
            if not vehicle.model_id or not vehicle.driver_id:
                continue

            avg_daily_km = vehicle._compute_average_daily_km()
            current_odometer = vehicle.odometer
            forecast_7d = current_odometer + (avg_daily_km * 7)
            forecast_14d = current_odometer + (avg_daily_km * 14)

            plans = self.env["fleet.vehicle.model.inspection.plan"].search(
                [("model_id", "=", vehicle.model_id.id)]
            )
            if not plans:
                continue

            items_7d = []
            items_14d = []
            trigger_inspection = False
            min_target_km = float('inf')
            trigger_item_name = ""

            for plan in plans:
                last_line = self.env["fleet.vehicle.inspection.line"].search(
                    [
                        ("inspection_id.vehicle_id", "=", vehicle.id),
                        ("inspection_item_id", "=", plan.item_id.id),
                        ("inspection_id.state", "!=", "cancel"),
                    ],
                    order="create_date desc",
                    limit=1,
                )

                last_km = (
                    last_line.inspection_id.odometer
                    if last_line and last_line.inspection_id.odometer
                    else 0.0
                )
                target_km = last_km + plan.interval_km

                if forecast_7d >= target_km:
                    items_7d.append(plan.item_id)
                    if plan.criticality == 'alta':
                        trigger_inspection = True
                        if target_km < min_target_km:
                            min_target_km = target_km
                            trigger_item_name = plan.item_id.name
                elif forecast_14d >= target_km:
                    items_14d.append(plan.item_id)

            if not trigger_inspection:
                continue

            communicated_km = min_target_km if min_target_km != float('inf') else forecast_7d

            all_lines = self.env["fleet.vehicle.inspection.line"].search(
                [
                    ("inspection_id.vehicle_id", "=", vehicle.id),
                    ("inspection_id.state", "!=", "cancel"),
                ],
                order="create_date desc"
            )
            
            seen_items = set()
            failed_items = []
            for line in all_lines:
                if line.inspection_item_id.id not in seen_items:
                    seen_items.add(line.inspection_item_id.id)
                    if line.result == 'failure':
                        failed_items.append(line.inspection_item_id)

            if avg_daily_km > 0 and min_target_km != float('inf'):
                days_to_add = max(0, (min_target_km - current_odometer) / avg_daily_km)
            else:
                days_to_add = 0

            inspection_date = exact_date_8am(today, days_to_add)
            all_items = items_7d + items_14d + list(failed_items)
            unique_items = list(set(all_items))

            if unique_items:

                rows_html = ""
                for item in unique_items:
                    item_parts = self.env['vehicle.part'].search([
                        ('vehicle_model_id', '=', vehicle.model_id.id),
                        ('inspection_item_id', '=', item.id)
                    ])
                    for part in item_parts:
                        if part.part_number:
                            product_names = ", ".join(part.product_ids.mapped('name'))
                            rows_html += f"<tr><td>{item.name}</td><td>{part.part_number}</td><td>{part.qty}</td><td>{product_names}</td></tr>"

                if rows_html:
                    note_html = "<table class='table table-bordered'><thead><tr><th>Item</th><th>Código da Peça</th><th>Quantidade</th><th>Produtos Relacionados</th></tr></thead><tbody>" + rows_html + "</tbody></table>"
                else:
                    note_html = ""

                trigger_info = ""
                if trigger_item_name:
                    remaining_km = min_target_km - current_odometer
                    val_alvo = "{:,.2f}".format(min_target_km).replace(',', 'X').replace('.', ',').replace('X', '.')
                    val_faltam = "{:,.2f}".format(remaining_km).replace(',', 'X').replace('.', ',').replace('X', '.')
                    
                    trigger_info = (
                        f"<p><strong>Item crítico disparador:</strong> {trigger_item_name}<br/>"
                        f"<strong>KM Alvo:</strong> {val_alvo}<br/>"
                        f"<strong>Faltam:</strong> {val_faltam} km</p>"
                    )

                inspection = self.env["fleet.vehicle.inspection"].create(
                    {
                        "vehicle_id": vehicle.id,
                        "date_inspected": inspection_date,
                        "note": note_html,
                        "odometer": vehicle.odometer,
                        "inspection_line_ids": [
                            (0, 0, {"inspection_item_id": item.id}) for item in unique_items
                        ],
                    }
                )

                if trigger_info:
                    inspection.message_post(body=trigger_info)

                inspection.message_subscribe(partner_ids=[vehicle.driver_id.id])

                vehicle._send_whatsapp_invite(
                    inspection, unique_items, communicated_km, inspection_date
                )
                vehicle._send_email_invite(
                    inspection, unique_items, communicated_km, inspection_date
                )

    def _send_whatsapp_invite(self, inspection, items, forecast_km, date):
        self.ensure_one()
        template = self.env.ref(
            "vehicle_scheduled_inspection.wa_template_convite_manutencao_programada",
            raise_if_not_found=False,
        )
        if not template:
            return

        item_names = [i.name.lower() for i in items]

        def match_item(keywords):
            return any(all(k in n for k in keywords) for n in item_names)

        v5 = (
            "✅"
            if match_item(["óleo", "motor"]) or match_item(["oleo", "motor"])
            else "❌"
        )
        v6 = (
            "✅"
            if match_item(["filtro", "óleo"]) or match_item(["filtro", "oleo"])
            else "❌"
        )
        v7 = (
            "✅"
            if match_item(["filtro", "combustível"])
            or match_item(["filtro", "combustivel"])
            else "❌"
        )
        v8 = "✅" if match_item(["filtro", "ar"]) else "❌"

        known_items = [
            "óleo",
            "oleo",
            "filtro de óleo",
            "filtro de oleo",
            "filtro de combustível",
            "filtro de combustivel",
            "filtro de ar",
        ]
        v9 = (
            "✅"
            if any(not any(k in n for k in known_items) for n in item_names)
            else "❌"
        )

        body = template.body or ""
        body = body.replace("{{1}}", self.driver_id.name or "")
        body = body.replace("{{2}}", self.name or "")
        body = body.replace("{{3}}", str(int(forecast_km)))
        body = body.replace("{{4}}", date.strftime("%d/%m/%Y"))
        body = body.replace("{{5}}", v5)
        body = body.replace("{{6}}", v6)
        body = body.replace("{{7}}", v7)
        body = body.replace("{{8}}", v8)
        body = body.replace("{{9}}", v9)

        phone = self.driver_id.mobile or self.driver_id.phone
        if not phone:
            return

        import json
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": self.driver_id.name or " "},
                    {"type": "text", "text": self.name or " "},
                    {"type": "text", "text": str(int(forecast_km))},
                    {"type": "text", "text": date.strftime("%d/%m/%Y")},
                    {"type": "text", "text": v5},
                    {"type": "text", "text": v6},
                    {"type": "text", "text": v7},
                    {"type": "text", "text": v8},
                    {"type": "text", "text": v9},
                ]
            }
        ]

        msg = self.env["whatsapp.message"].create(
            {
                "body": body,
                "mobile_number": phone,
                "partner_id": self.driver_id.id,
                "template_id": template.id,
                "res_model": "fleet.vehicle.inspection",
                "res_id": inspection.id,
                "status": "draft",
                "components_json": json.dumps(components),
            }
        )
        msg.action_send()

        body_log = _("<b>WhatsApp Message Sent</b><br/>%s") % body.replace(
            "\n", "<br/>"
        )
        inspection.message_post(body=body_log)

    def _send_email_invite(self, inspection, items, forecast_km, date):
        self.ensure_one()
        template = self.env.ref(
            "vehicle_scheduled_inspection.mail_template_convite_manutencao_programada",
            raise_if_not_found=False,
        )
        if template:
            template.with_context(
                forecast_km=int(forecast_km),
                inspection_date=date.strftime("%d/%m/%Y"),
                inspection_items=items,
            ).send_mail(inspection.id, force_send=True)
