from odoo import fields, models


class VehiclePart(models.Model):
    _name = "vehicle.part"
    _description = "Vehicle Part"
    vehicle_model_id = fields.Many2one("fleet.vehicle.model", string="Vehicle Model", required=True)
    inspection_item_id = fields.Many2one("fleet.vehicle.inspection.item", string="Inspection Item", required=True)
    name = fields.Char(related="vehicle_model_id.name", store=True, readonly=True, string="Model Name")
    part_number = fields.Char(string="Código da Peça", required=True)
    qty = fields.Integer(string="Quantidade Aplicada", default=1)
    group_id = fields.Many2one("vehicle.part.group", string="Group")
    category_id = fields.Many2one("vehicle.part.category", string="Category")
    product_ids = fields.Many2many(
        "product.template",
        "vehicle_part_product_rel",
        "part_id",
        "product_id",
        string="Products",
    )
