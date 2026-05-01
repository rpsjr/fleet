from dateutil.relativedelta import relativedelta

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

        def next_business_day(date):
            next_date = date + relativedelta(days=1)
            while next_date.weekday() >= 5:  # 5: Saturday, 6: Sunday
                next_date += relativedelta(days=1)
            return next_date

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

            for plan in plans:
                last_line = self.env["fleet.vehicle.inspection.line"].search(
                    [
                        ("inspection_id.vehicle_id", "=", vehicle.id),
                        ("item_id", "=", plan.item_id.id),
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
                elif forecast_14d >= target_km:
                    items_14d.append(plan.item_id)

            if items_7d:
                inspection_date = next_business_day(today)
                all_items = items_7d + items_14d
                unique_items = list(set(all_items))

                parts = self.env["vehicle.part"].search(
                    [
                        ("vehicle_model_id", "=", vehicle.model_id.id),
                        ("inspection_item_id", "in", [i.id for i in unique_items]),
                    ]
                )

                note_html = "<table class='table table-bordered'><thead><tr><th>Item</th><th>Código da Peça</th><th>Produtos Relacionados</th></tr></thead><tbody>"
                for part in parts:
                    product_names = ", ".join(part.product_ids.mapped("name"))
                    note_html += f"<tr><td>{part.inspection_item_id.name}</td><td>{part.part_number}</td><td>{product_names}</td></tr>"
                note_html += "</tbody></table>"

                inspection = self.env["fleet.vehicle.inspection"].create(
                    {
                        "vehicle_id": vehicle.id,
                        "date_inspected": inspection_date,
                        "note": note_html,
                        "odometer": vehicle.odometer,
                        "inspection_line_ids": [
                            (0, 0, {"item_id": item.id}) for item in unique_items
                        ],
                    }
                )

                inspection.message_subscribe(partner_ids=[vehicle.driver_id.id])

                vehicle._send_whatsapp_invite(
                    inspection, unique_items, forecast_7d, inspection_date
                )
                vehicle._send_email_invite(
                    inspection, unique_items, forecast_7d, inspection_date
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

        msg = self.env["whatsapp.message"].create(
            {
                "body": body,
                "mobile_number": phone,
                "partner_id": self.driver_id.id,
                "template_id": template.id,
                "res_model": "fleet.vehicle.inspection",
                "res_id": inspection.id,
                "status": "draft",
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
