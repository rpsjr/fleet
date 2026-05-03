{
    "name": "Vehicle Scheduled Inspection",
    "version": "13.0.1.1.0",
    "category": "Fleet",
    "summary": "Schedule vehicle inspections based on maintenance plan.",
    "author": "Odoo Community Association (OCA)",
    "license": "AGPL-3",
    "depends": [
        "fleet",
        "fleet_vehicle_inspection",
        "fleet_vehicle_model_inspection_plan",
        "vehicle_parts_catalogue",
        "meta_whatsapp",
    ],
    "data": ["data/cron_data.xml", "data/mail_template_data.xml",],
    "installable": True,
}
