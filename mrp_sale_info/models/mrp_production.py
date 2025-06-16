# Copyright 2016 Antiun Ingenieria S.L. - Javier Iniesta
# Copyright 2019 Rubén Bravo <rubenred18@gmail.com>
# Copyright 2020 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    source_procurement_group_id = fields.Many2one(
        comodel_name="procurement.group",
        readonly=True,
    )
    sale_id = fields.Many2one(
        comodel_name="sale.order",
        string="Sale order",
        readonly=True,
        store=True,
        related="source_procurement_group_id.sale_id",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        related="sale_id.partner_id",
        string="Customer",
        store=True,
    )
    commitment_date = fields.Datetime(
        related="sale_id.commitment_date", string="Commitment Date", store=True
    )
    client_order_ref = fields.Char(
        related="sale_id.client_order_ref",
        string="Customer Reference",
        store=True,
    )

    def action_merge(self):
        # This method determines the context for the create method.
        # We need to identify the correct source_procurement_group_id here.
        _logger.info(f"Custom action_merge called for MOs: {self.ids}")

        source_group_to_set = None  # This will be the procurement.group record
        common_sale_order_from_source_group = None

        if self:
            # Take the source_procurement_group_id
            # of the first production order as a basis
            # Assuming the field 'source_procurement_group_id' exists.
            # If the sale_id is related to "procurement_group_id.sale_id",
            # then we target that.
            # Given the definition "source_procurement_group_id.sale_id",
            #  we must target this field.

            first_production = self[0]

            # Check if the field source_procurement_group_id exists
            if (
                hasattr(first_production, "source_procurement_group_id")
                and first_production.source_procurement_group_id
            ):
                potential_source_group = first_production.source_procurement_group_id

                if potential_source_group.sale_id:
                    common_sale_order_from_source_group = potential_source_group.sale_id
                    # Check if all MOs to be merged ultimately refer
                    # to the same sale_id via their source_procurement_group_id.
                    all_match_sale_id = all(
                        hasattr(prod, "source_procurement_group_id")
                        and prod.source_procurement_group_id
                        and prod.source_procurement_group_id.sale_id
                        == common_sale_order_from_source_group
                        for prod in self
                    )

                    if all_match_sale_id:
                        source_group_to_set = potential_source_group
                        _logger.info(
                            f"All MOs lead to the same Sales Order "
                            f"({common_sale_order_from_source_group.name}, "
                            f"ID: {common_sale_order_from_source_group.id}) "
                            f"via their source_procurement_group_id. "
                            f"Using source_procurement_group_id: "
                            f"{source_group_to_set.name} "
                            f"(ID: {source_group_to_set.id})."
                        )
                    else:
                        _logger.warning(
                            "Not all MOs to be merged have a consistent "
                            "Sale Order via their source_procurement_group_id. "
                            "The source_procurement_group_id will not be set on "
                            "the merged MO."
                        )
                        # common_sale_order_from_source_group still exists,
                        # but source_group_to_set does not
                else:
                    _logger.info(
                        f"The source_procurement_group_id on the first MO "
                        f"({first_production.id}) does not have a sale_id."
                    )
            else:
                _logger.info(
                    f"Field 'source_procurement_group_id' not found "
                    f"or not set on the first MO ({first_production.id}). "
                    f"Ensure mrp_sale_info is installed and the field is "
                    f"populated on original MOs."
                )

        ctx = self.env.context.copy()
        # We now pass the ID of the source_procurement_group,
        # not directly the sale_id
        if (
            source_group_to_set and common_sale_order_from_source_group
        ):  # Only if we have a consistent group AND SO
            ctx["default_source_procurement_group_id_for_merge"] = (
                source_group_to_set.id
            )
            _logger.info(
                f"Context set: default_source_procurement_group_id_for_merge = "
                f"{source_group_to_set.id}"
            )
        else:
            ctx.pop("default_source_procurement_group_id_for_merge", None)

        return super(MrpProduction, self.with_context(**ctx)).action_merge()

    @api.model_create_multi
    def create(self, vals_list):
        new_vals_list_for_super = []
        for vals_item in vals_list:
            vals = dict(vals_item)  # Work with a copy
            _logger.info(f"Custom create. Context: {self.env.context}")
            _logger.info(f"Original vals for create: {vals}")

            if "default_source_procurement_group_id_for_merge" in self.env.context:
                source_group_id_from_context = self.env.context.get(
                    "default_source_procurement_group_id_for_merge"
                )
                if source_group_id_from_context:
                    # Add the source_procurement_group_id to vals.
                    # The related sale_id field will automatically
                    # be correct because of this.
                    vals["source_procurement_group_id"] = source_group_id_from_context
                    _logger.info(
                        f"source_procurement_group_id "
                        f"{source_group_id_from_context} "
                        f"added to vals from context."
                    )
                    # Remove any directly passed sale_id,
                    # as it's a related field.
                    if "sale_id" in vals:
                        vals.pop("sale_id")
                        _logger.info(
                            "Removed direct 'sale_id' from vals "
                            "as it's a related field."
                        )
                else:
                    # If context key is present but None/False,
                    # ensure fields are not set or are removed.
                    vals.pop("source_procurement_group_id", None)
                    if "sale_id" in vals:
                        vals.pop("sale_id")
                    _logger.info(
                        "default_source_procurement_group_id_for_merge in "
                        "context was None/False, fields not set/removed."
                    )

            new_vals_list_for_super.append(vals)

        # Remove our context key for the super() call to prevent side effects
        productions = super(
            MrpProduction,
            self.with_context(default_source_procurement_group_id_for_merge=None),
        ).create(new_vals_list_for_super)

        # Log the state after creation
        # Check if context was set, to only log for the merge flow
        # covers case where it was explicitly None
        # or not present at all if no valid group was found
        is_merge_flow_context = (
            "default_source_procurement_group_id_for_merge" in self.env.context
        )

        if is_merge_flow_context:
            for production in productions:
                source_group_info = "None"
                # Check if the field exists before trying to access it,
                # good practice after create
                if (
                    hasattr(production, "source_procurement_group_id")
                    and production.source_procurement_group_id
                ):
                    source_group_info = (
                        f"{production.source_procurement_group_id.name} "
                        f"(ID: {production.source_procurement_group_id.id})"
                    )

                sale_order_info = "None"
                if production.sale_id:
                    sale_order_info = (
                        f"{production.sale_id.name} (ID: {production.sale_id.id})"
                    )

                _logger.info(
                    f"New production order {production.name} "
                    f"(ID: {production.id}) created. "
                    f"Source Procurement Group: {source_group_info}. "
                    f"Resulting Related Sale Order: {sale_order_info}."
                )
        return productions
