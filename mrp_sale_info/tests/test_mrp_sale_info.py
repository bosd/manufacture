# Copyright 2020 Tecnativa - Pedro M. Baeza
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

import logging

from odoo.tests import common, tagged

_logger = logging.getLogger(__name__)


@tagged("post_install", "-at_install")
class TestMrpSaleInfo(common.TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Ensure default warehouse exists for procurement
        cls.warehouse = cls.env.ref("stock.warehouse0")
        cls.stock_location_stock = cls.env.ref("stock.stock_location_stock")
        cls.stock_location_production = cls.env["stock.location"].search(
            [("usage", "=", "production")], limit=1
        )
        if not cls.stock_location_production:
            cls.stock_location_production = cls.env["stock.location"].create(
                {"name": "Production", "usage": "production"}
            )

        # Make sure routes are active and consistent
        cls.route_manufacture = cls.env.ref("mrp.route_warehouse0_manufacture")
        cls.route_manufacture.active = True  # Ensure it's active
        cls.route_mto = cls.env.ref("stock.route_warehouse0_mto")
        cls.route_mto.active = True  # Ensure it's active

        cls.component_product = cls.env["product.product"].create(
            {
                "name": "Test Component (mrp_sale_info)",
                "type": "consu",
                "is_storable": True,
                "uom_id": cls.env.ref("uom.product_uom_unit").id,
                "uom_po_id": cls.env.ref("uom.product_uom_unit").id,
                "standard_price": 5.0,
                "list_price": 10.0,
            }
        )

        # Product setup
        cls.product = cls.env["product.product"].create(
            {
                "name": "Test mrp_sale_info product",
                "type": "consu",
                "is_storable": True,
                "route_ids": [
                    (4, cls.route_manufacture.id),
                    (4, cls.route_mto.id),
                ],
            }
        )
        cls.test_byproduct_product = cls.env["product.product"].create(
            {
                "name": "Test Byproduct for Merge",
                "type": "consu",
                "is_storable": True,
                "uom_id": cls.env.ref("uom.product_uom_unit").id,
                "uom_po_id": cls.env.ref("uom.product_uom_unit").id,
                "standard_price": 0.1,
                "list_price": 0.2,
            }
        )

        cls.bom = cls.env["mrp.bom"].create(
            {
                "product_tmpl_id": cls.product.product_tmpl_id.id,
                "operation_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Test operation",
                            "workcenter_id": cls.env.ref("mrp.mrp_workcenter_3").id,
                        },
                    )
                ],
                "bom_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.component_product.id,
                            "product_qty": 1,
                            "product_uom_id": cls.component_product.uom_id.id,
                        },
                    )
                ],
                "byproduct_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.test_byproduct_product.id,
                            "product_qty": 0.1,
                            "product_uom_id": cls.test_byproduct_product.uom_id.id,  # noqa
                            "cost_share": 0,
                        },
                    )
                ],
            }
        )
        cls.partner = cls.env["res.partner"].create({"name": "Test client"})

        # Setup initial stock for the newly created component
        cls.env["stock.quant"]._update_available_quantity(
            cls.component_product,
            cls.stock_location_stock,
            100,  # Sufficient quantity
        )
        cls.env["stock.quant"]._update_available_quantity(
            cls.test_byproduct_product,
            cls.stock_location_production,  # Byproducts are produced into here
            100,  # Sufficient quantity
        )

    def _create_mo_for_merge_test(self, sale_order_record, product_qty=1.0):
        """
        Helper to create a fresh MO for a sale order, explicitly linking its
        source procurement group for merge testing, and confirming it.
        """
        # Ensure SO is confirmed
        if sale_order_record.state == "draft":
            sale_order_record.action_confirm()

        # Ensure procurement group exists for the SO
        proc_group = sale_order_record.procurement_group_id
        if not proc_group:
            _logger.warning(
                f"SO {sale_order_record.name} has no procurement group. "
                f"Creating one for test."
            )
            proc_group = self.env["procurement.group"].create(
                {
                    "name": sale_order_record.name,
                    "move_type": "direct",
                    "sale_id": sale_order_record.id,
                }
            )
            sale_order_record.write({"procurement_group_id": proc_group.id})
            self.env.invalidate_all()  # Ensure cache updated

        # Run scheduler (optional, but good habit)
        proc_group.run_scheduler()
        self.env.invalidate_all()  # Ensure cache updated after scheduler

        # Explicitly create the MO
        mrp_picking_type = self.env["stock.picking.type"].search(
            [
                ("code", "=", "mrp_operation"),
                ("warehouse_id", "=", self.warehouse.id),
            ],
            limit=1,
        )
        self.assertTrue(
            mrp_picking_type,
            "Manufacturing picking type ('mrp_operation') not found for MO "
            "creation fallback.",
        )

        mo = self.env["mrp.production"].create(
            {
                "product_id": self.product.id,
                "product_qty": product_qty,
                "bom_id": self.bom.id,
                "product_uom_id": self.product.uom_id.id,
                "origin": sale_order_record.name,
                "state": "draft",
                "picking_type_id": mrp_picking_type.id,
                "location_src_id": self.stock_location_stock.id,
                "location_dest_id": self.stock_location_production.id,
                "procurement_group_id": proc_group.id,
                "source_procurement_group_id": proc_group.id,
            }
        )

        # MO moves are generated when it goes from draft -> confirmed.
        if mo.state == "draft":
            mo.action_confirm()
        self.env.invalidate_all()
        mo = self.env["mrp.production"].browse(mo.id)

        self.assertTrue(mo, f"MO for SO {sale_order_record.name} must exist for test.")
        self.assertEqual(
            mo.state, "confirmed", "MO should be confirmed after creation."
        )
        self.assertTrue(
            mo.source_procurement_group_id,
            "MO should have a source procurement group explicitly set.",
        )
        self.assertEqual(
            mo.source_procurement_group_id,
            proc_group,
            "MO source group should link to original SO's procurement group.",
        )
        self.assertEqual(
            mo.sale_id,
            sale_order_record,
            "MO sale_id should match original SO.",
        )

        # Assert that raw material moves and finished product moves are
        # now present.
        self.assertTrue(
            mo.move_raw_ids,
            "MO raw material moves should be generated after confirmation.",
        )
        self.assertTrue(
            mo.move_finished_ids,
            "MO finished product moves should be generated after confirmation.",
        )
        # Double check that the finished moves include both the main product
        # and byproduct
        self.assertTrue(
            mo.move_finished_ids.filtered(lambda m: m.product_id == self.product),
            "Main finished product move should be present.",
        )
        self.assertTrue(
            mo.move_finished_ids.filtered(
                lambda m: m.product_id == self.test_byproduct_product
            ),
            "Byproduct move should be present.",
        )

        # Prepare stock for MO components (before action_assign)
        for bom_line in mo.bom_id.bom_line_ids:
            self.env["stock.quant"]._update_available_quantity(
                bom_line.product_id,
                self.stock_location_stock,
                bom_line.product_qty * mo.product_qty + 10,
            )
        self.env.invalidate_all()

        # Now that moves are generated and stock is ready,
        # proceed with action_assign.
        mo.action_assign()
        self.env.invalidate_all()
        mo = self.env["mrp.production"].browse(mo.id)

        return mo

    def test_mrp_sale_info(self):
        # Create a fresh sale order for this test
        sale_order_test = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "client_order_ref": "SO_TEST_MRP_INFO",
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 1,
                            "price_unit": 1,
                        },
                    ),
                ],
            }
        )
        sale_order_test.action_confirm()  # Confirm SO to generate MO

        # Use helper to ensure MO is created and linked
        production = self._create_mo_for_merge_test(sale_order_test, product_qty=1)

        self.assertEqual(production.sale_id, sale_order_test)
        self.assertEqual(production.partner_id, self.partner)
        self.assertEqual(production.client_order_ref, sale_order_test.client_order_ref)

    def test_merge_mo_consistent_sale_order(self):
        """
        Test merging multiple MOs that share the same underlying Sale Order,
        ensuring the merged MO gets the correct source_procurement_group_id.
        """
        # Create a primary Sale Order (used as the common source for MOs)
        original_so = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "client_order_ref": "SO_CONSISTENT_PRIMARY",
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 3,
                            "price_unit": 1,
                        },
                    )
                ],
            }
        )
        original_so.action_confirm()

        proc_group_orig = original_so.procurement_group_id
        self.assertTrue(
            proc_group_orig, "Procurement group should exist for primary SO."
        )

        mo1_from_orig_so = self._create_mo_for_merge_test(original_so, product_qty=1)
        mo2_from_orig_so = self._create_mo_for_merge_test(
            original_so,
            product_qty=2,  # Different quantity for the second MO
        )

        # Ensure MOs are in 'confirmed' state before merge
        # (handled by helper, good check)
        self.assertEqual(mo1_from_orig_so.state, "confirmed")
        self.assertEqual(mo2_from_orig_so.state, "confirmed")

        # Merge the two MOs
        productions_to_merge = mo1_from_orig_so | mo2_from_orig_so
        merged_mo_result = productions_to_merge.action_merge()

        # Get the newly created MO after merge
        if isinstance(merged_mo_result, dict) and "res_id" in merged_mo_result:
            merged_mo_record = self.env["mrp.production"].browse(
                merged_mo_result["res_id"]
            )
        else:  # Fallback search
            merged_mo_record = self.env["mrp.production"].search(
                [
                    ("id", "not in", (mo1_from_orig_so + mo2_from_orig_so).ids),
                    ("product_id", "=", self.product.id),
                    (
                        "product_qty",
                        "=",
                        mo1_from_orig_so.product_qty + mo2_from_orig_so.product_qty,
                    ),
                    ("origin", "like", original_so.name),
                ],
                order="id desc",
                limit=1,
            )

        self.assertTrue(merged_mo_record, "Merged MO should be created.")
        self.assertEqual(
            merged_mo_record.product_qty,
            mo1_from_orig_so.product_qty + mo2_from_orig_so.product_qty,
            "Merged MO quantity is incorrect.",
        )

        # Assert source_procurement_group_id is set
        self.assertTrue(
            merged_mo_record.source_procurement_group_id,
            "Merged MO should have source_procurement_group_id.",
        )
        self.assertEqual(
            merged_mo_record.source_procurement_group_id,
            proc_group_orig,
            "Merged MO source group should match original SO's" " procurement group.",
        )
        self.assertEqual(
            merged_mo_record.sale_id,
            original_so,
            "Merged MO sale_id should match original SO.",
        )
        self.assertEqual(
            merged_mo_record.partner_id,
            original_so.partner_id,
            "Merged MO partner_id should match original SO's partner.",
        )
        self.assertEqual(
            merged_mo_record.client_order_ref,
            original_so.client_order_ref,
            "Merged MO client_order_ref should match original SO's ref.",
        )

        # Assert original MOs are cancelled or deleted
        self.env.invalidate_all()
        mo1_from_orig_so = self.env["mrp.production"].browse(
            mo1_from_orig_so.id
        )  # Re-fetch
        mo2_from_orig_so = self.env["mrp.production"].browse(
            mo2_from_orig_so.id
        )  # Re-fetch

        self.assertIn(
            mo1_from_orig_so.state,
            ["cancel", "done"],
            "Original MO 1 should be cancelled or done.",
        )
        self.assertIn(
            mo2_from_orig_so.state,
            ["cancel", "done"],
            "Original MO 2 should be cancelled or done.",
        )

    def test_merge_mo_inconsistent_sale_order(self):
        """
        Test merging multiple MOs that DO NOT share the same underlying
        Sale Order,
        ensuring the merged MO does NOT get source_procurement_group_id set.
        """
        # Create two MOs coming from DIFFERENT Sale Orders
        sale_order_diff_1 = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "client_order_ref": "SO_DIFF_1",
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 1,
                            "price_unit": 1,
                        },
                    )
                ],
            }
        )
        mo_diff_1 = self._create_mo_for_merge_test(sale_order_diff_1, product_qty=1)

        sale_order_diff_2 = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "client_order_ref": "SO_DIFF_2",
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 2,
                            "price_unit": 1,
                        },
                    )
                ],
            }
        )
        mo_diff_2 = self._create_mo_for_merge_test(sale_order_diff_2, product_qty=2)

        # Assert they have different procurement groups/sale_ids
        self.assertNotEqual(
            mo_diff_1.source_procurement_group_id.sale_id,
            mo_diff_2.source_procurement_group_id.sale_id,
            "MOs should come from different Sale Orders for this test.",
        )

        # Merge the two MOs
        # merged_mo = (mo_diff_1 + mo_diff_2).action_merge()

        productions_to_merge = mo_diff_1 | mo_diff_2
        merged_mo = productions_to_merge.action_merge()

        # Get the newly created MO after merge
        if isinstance(merged_mo, dict) and "res_id" in merged_mo:
            merged_mo_record = self.env["mrp.production"].browse(merged_mo["res_id"])
        else:  # Fallback search
            merged_mo_record = self.env["mrp.production"].search(
                [
                    ("id", "not in", (mo_diff_1 + mo_diff_2).ids),
                    ("product_id", "=", self.product.id),
                    (
                        "product_qty",
                        "=",
                        mo_diff_1.product_qty + mo_diff_2.product_qty,
                    ),
                    (
                        "origin",
                        "not ilike",
                        mo_diff_1.origin,
                    ),  # New MO should NOT just take first origin
                    # if inconsistent
                ],
                order="id desc",
                limit=1,
            )

        self.assertTrue(merged_mo_record, "Merged MO should be created.")

        # Assert source_procurement_group_id is NOT set (or is False)
        self.assertFalse(
            merged_mo_record.source_procurement_group_id,
            "Merged MO should NOT have source_procurement_group_id "
            "due to inconsistent sources.",
        )
        self.assertFalse(
            merged_mo_record.sale_id, "Merged MO sale_id should NOT be set."
        )
        self.assertFalse(
            merged_mo_record.partner_id,
            "Merged MO partner_id should NOT be set.",
        )
        self.assertFalse(
            merged_mo_record.client_order_ref,
            "Merged MO client_order_ref should NOT be set.",
        )

        # Assert original MOs are cancelled or deleted
        self.env.invalidate_all()
        mo_diff_1 = self.env["mrp.production"].browse(mo_diff_1.id)
        mo_diff_2 = self.env["mrp.production"].browse(mo_diff_2.id)

        self.assertIn(
            mo_diff_1.state,
            ["cancel", "done"],
            "Original MO 1 should be cancelled or done.",
        )
        self.assertIn(
            mo_diff_2.state,
            ["cancel", "done"],
            "Original MO 2 should be cancelled or done.",
        )

    def test_mrp_workorder(self):
        prev_workorders = self.env["mrp.workorder"].search([])

        sale_order_test = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "client_order_ref": "SO_TEST_WORKORDER",
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.product.id,
                            "product_uom_qty": 1,
                            "price_unit": 1,
                        },
                    )
                ],
            }
        )
        sale_order_test.action_confirm()
        workorder = (
            self.env["mrp.production"].search([]).workorder_ids - prev_workorders
        )
        self.assertEqual(workorder.sale_id, sale_order_test)
        self.assertEqual(workorder.partner_id, self.partner)
        self.assertEqual(workorder.client_order_ref, sale_order_test.client_order_ref)
