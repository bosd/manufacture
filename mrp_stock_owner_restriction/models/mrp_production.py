# Copyright 2023 Quartile
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).


# bosd edit
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)
# bos edit


class MrpProduction(models.Model):
    _inherit = "mrp.production"

    owner_id = fields.Many2one(
        "res.partner",
        "Assign Owner",
        readonly=True,
        check_company=True,
        help="Produced products will be assigned to this owner.",
    )
    owner_restriction = fields.Selection(related="picking_type_id.owner_restriction")

    @api.model_create_multi
    def create(self, vals_list):
        """
        Override create to ensure owner_id is set correctly, especially when
        called from a merge operation with a specific owner in context.
        """
        new_vals_list_for_super = []
        for vals_item in vals_list:
            vals = dict(vals_item)  # Work with a copy
            _logger.info(f"MRP Create - Original vals: {vals}")
            _logger.info(f"MRP Create - Context: {self.env.context}")

            # Check if an owner_id is being passed from the merge action's context
            if "default_owner_id_for_merged_mo" in self.env.context:
                owner_id_from_context = self.env.context.get(
                    "default_owner_id_for_merged_mo"
                )
                if owner_id_from_context:
                    vals["owner_id"] = owner_id_from_context
                    _logger.info(
                        f"MRP Create - Set owner_id to {owner_id_from_context}\n"
                        " from merge context for vals: {vals}"
                    )
                else:
                    # Context key was present but no valid ID (e.g., None or False)
                    # Ensure owner_id is not in vals
                    # or is explicitly None if that's intended
                    vals.pop("owner_id", None)
                    _logger.info(
                        "MRP Create - owner_id removed/not set from merge context "
                        "(context value was None/False)."
                    )

            new_vals_list_for_super.append(vals)

        # Call super with potentially modified vals_list
        # Important: Remove our custom context key before calling super to prevent
        # unintended side effects if create is called recursively or by other processes.
        records = super(
            MrpProduction, self.with_context(default_owner_id_for_merged_mo=None)
        ).create(new_vals_list_for_super)

        # Post-creation logic (e.g., setting owner for 'unassigned_owner' picking types)
        # This part of your original logic can remain as a fallback
        #  if no owner was set by context.
        for record in records:
            if (
                record.picking_type_id
                and record.picking_type_id.owner_restriction == "unassigned_owner"
                and not record.owner_id  # Check if owner_id is still not set
            ):
                record.owner_id = self.env.company.partner_id
                _logger.info(
                    f"MRP Create - Set owner_id to company partner for MO {record.name}"
                    " due to unassigned_owner policy."
                )

            _logger.info(
                f"MRP Create - Final state for MO {record.name} (ID: {record.id}):\n"
                "owner_id: {record.owner_id.id if record.owner_id else 'None'}"
            )

        return records

    def write(self, vals):
        if "owner_id" in vals:
            for production in self:
                if production.owner_restriction in (
                    "unassigned_owner",
                    "picking_partner",
                ):
                    production.move_line_raw_ids.unlink()
        return super().write(vals)

    def action_merge(self):
        _logger.info(f"Custom action_merge called for MOs: {self.ids}")

        common_owner_id = None
        if self:
            # Check if all MOs to be merged have an owner_id and if it's the same
            first_mo_owner = self[0].owner_id
            if first_mo_owner:  # If the first MO has an owner
                all_same_owner = all(prod.owner_id == first_mo_owner for prod in self)
                if all_same_owner:
                    common_owner_id = first_mo_owner.id
                    _logger.info(
                        "All MOs to be merged share the same owner_id:\n"
                        "{common_owner_id} ({first_mo_owner.name})"
                    )
                else:
                    _logger.info(
                        "MOs to be merged have different owner_ids. "
                        "Merged MO will not inherit an owner from this logic.\n"
                        "First MO owner: {first_mo_owner.name}"
                    )
            else:
                # Check if all MOs have no owner
                all_no_owner = all(not prod.owner_id for prod in self)
                if all_no_owner:
                    _logger.info(
                        "All MOs to be merged have no owner_id. "
                        "Merged MO will also have no owner_id from this logic."
                    )
                else:
                    _logger.info(
                        "MOs to be merged have mixed owner_id status\n"
                        " (some have, some don't). "
                        "Merged MO will not inherit an owner from this logic."
                    )

        ctx = self.env.context.copy()
        if common_owner_id:
            ctx["default_owner_id_for_merged_mo"] = common_owner_id
            _logger.info(
                "Context set for merge: "
                "default_owner_id_for_merged_mo = {common_owner_id}"
            )
        else:
            # Ensure the key is not in context if no common owner,
            # or if it was there from a previous operation
            ctx.pop("default_owner_id_for_merged_mo", None)
            _logger.info(
                "No common owner_id found for MOs to be merged,"
                " or first MO had no owner. "
                "Context key 'default_owner_id_for_merged_mo' not set or removed."
            )

        # Call the original action_merge with the potentially modified context.
        # The standard action_merge will internally call the .create() method,
        # which we have also overridden.
        return super(MrpProduction, self.with_context(**ctx)).action_merge()
