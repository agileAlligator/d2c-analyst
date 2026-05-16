"""Margin Watch agent — scans SKU-level contribution margin and proposes ₹-saving actions."""
import logging
from decimal import Decimal

from app.agents.base import BaseAgent, Proposal
from app.config import settings
from app.warehouse.metrics.catalog import query_metric

logger = logging.getLogger(__name__)

MARGIN_FLOOR = Decimal("0")         # flag SKUs with margin < 0


class MarginWatchAgent(BaseAgent):
    name = "margin_watch"

    def _execute(self):
        self.log("Starting margin scan for last 14 days...")

        current = query_metric(self.db, self.merchant_id, "contribution_margin", time_range="14d")
        self.log(f"Found {len(current.rows)} orders in last 14 days.")

        flagged = []
        for row in current.rows:
            order_id = row.get("order_number") or row.get("order_id")
            # Skip rows with no order number (pure refund entities or unresolved joins)
            if not order_id:
                continue
            revenue = Decimal(str(row.get("revenue") or 0))
            # Skip rows where revenue itself is negative (standalone refund, no matching order)
            if revenue < 0:
                continue
            margin = Decimal(str(row.get("contribution_margin") or 0))
            prov_ids = row.get("provenance_ids") or current.provenance_ids

            if margin < MARGIN_FLOOR:
                flagged.append({
                    "order_id": order_id,
                    "margin": margin,
                    "reason": "negative_margin",
                    "row": row,
                    "prov_ids": prov_ids,
                })

        self.log(f"Flagged {len(flagged)} orders with negative contribution margin.")

        # Proposal 1: Find worst courier by RTO rate
        self._propose_courier_switch()

        # Proposal 2: Flag high-spend / low-return ad sets
        self._propose_adset_pause()

        # Proposal 3: Negative margin orders → price increase signal
        if flagged:
            worst = sorted(flagged, key=lambda x: x["margin"])[:3]
            for item in worst:
                revenue = Decimal(str(item["row"].get("revenue") or 0))
                margin = item["margin"]
                shipping = Decimal(str(item["row"].get("shipping_cost") or 0))
                rto = Decimal(str(item["row"].get("rto_cost") or 0))

                impact = abs(float(margin))
                rev_f = float(revenue)
                row = item["row"]

                # Derive variant_id and per-unit price from line_items when available.
                # Shopify line_items is a list; we pick the item with the highest price
                # (the "primary" line item) to target the price adjustment.
                line_items = row.get("line_items") or []
                variant_id = None
                unit_price = revenue  # fallback: treat whole order as qty-1
                quantity = Decimal("1")
                if line_items:
                    primary = max(line_items, key=lambda li: float(li.get("price") or 0))
                    variant_id = primary.get("variant_id")
                    unit_price = Decimal(str(primary.get("price") or revenue))
                    quantity = Decimal(str(primary.get("quantity") or 1))
                    if quantity <= 0:
                        quantity = Decimal("1")

                # Per-unit price increase to cover the full margin gap.
                # new_unit_price = unit_price + (|margin| / quantity)
                per_unit_increase = abs(margin) / quantity
                new_unit_price = unit_price + per_unit_increase

                # entity_key targets the product variant, not the order.
                if variant_id:
                    entity_key = f"sku:{variant_id}"
                else:
                    # Fallback: we don't have a variant ID, flag the order instead.
                    entity_key = f"order:{item['order_id']}"

                self.emit_proposal(Proposal(
                    action_type="raise_price",
                    entity_key=entity_key,
                    expected_inr_impact=impact,
                    reasoning=(
                        f"Order {item['order_id']} has contribution margin of "
                        f"₹{margin:,.2f} (revenue ₹{revenue:,.2f}, "
                        f"shipping ₹{shipping:,.2f}, RTO cost ₹{rto:,.2f}). "
                        f"Raising the variant price from ₹{unit_price:,.2f} to "
                        f"₹{new_unit_price:,.2f} (+₹{per_unit_increase:,.2f}/unit × "
                        f"{quantity} units) would move this order to breakeven."
                    ),
                    provenance_ids=item["prov_ids"][:5],
                    would_do_api_call={
                        "connector": "shopify",
                        "endpoint": "PUT /admin/api/2024-01/variants/{variant_id}.json",
                        "body": {"variant": {"price": str(new_unit_price)}},
                        "NOT_SENT": True,
                    },
                ))

    def _propose_courier_switch(self):
        """Identify the courier with highest RTO rate and propose switching."""
        result = query_metric(self.db, self.merchant_id, "rto_rate",
                              group_by="courier", time_range="30d")
        if not result.rows:
            self.log("No shipment data for courier analysis.")
            return

        sorted_couriers = sorted(
            [r for r in result.rows if r.get("total_shipments", 0) >= settings.min_shipments_for_courier_signal],
            key=lambda r: float(r.get("rto_rate") or 0),
            reverse=True,
        )
        if not sorted_couriers:
            return

        worst = sorted_couriers[0]
        best = sorted_couriers[-1] if len(sorted_couriers) > 1 else None
        rto_rate = float(worst.get("rto_rate") or 0)
        rto_count = int(worst.get("rto_count") or 0)
        courier = worst.get("courier", "unknown")

        # Estimate impact: each RTO costs rto_unit_cost_inr (config) in reverse logistics + lost COD
        est_impact = rto_count * settings.rto_unit_cost_inr

        self.log(f"Courier '{courier}' has RTO rate {rto_rate:.1%} ({rto_count} RTOs).")

        self.emit_proposal(Proposal(
            action_type="switch_courier",
            entity_key=f"courier:{courier}",
            expected_inr_impact=est_impact,
            reasoning=(
                f"Courier '{courier}' has an RTO rate of {rto_rate:.1%} "
                f"({rto_count} returns in 30 days). "
                + (
                    f"Switching to '{best.get('courier')}' "
                    f"(RTO rate {float(best.get('rto_rate') or 0):.1%}) "
                    f"could save ~₹{est_impact:,.0f}/month." if best else ""
                )
            ),
            provenance_ids=result.provenance_ids[:5],
            would_do_api_call={
                "connector": "shiprocket",
                "action": "update_courier_preference",
                "body": {"preferred_courier": best.get("courier") if best else "auto"},
                "NOT_SENT": True,
            },
        ))

    def _propose_adset_pause(self):
        """Flag ad sets with high spend but low ROAS."""
        spend_result = query_metric(self.db, self.merchant_id, "ad_spend",
                                    group_by="campaign", time_range="14d")
        if not spend_result.rows:
            self.log("No ad spend data available.")
            return

        revenue_result = query_metric(self.db, self.merchant_id, "revenue",
                                      time_range="14d")
        total_revenue = sum(float(r.get("revenue") or 0) for r in revenue_result.rows)
        total_spend = sum(float(r.get("ad_spend") or 0) for r in spend_result.rows)

        if total_spend == 0:
            return

        blended_roas = total_revenue / total_spend if total_spend else 0
        self.log(f"Blended ROAS (14d): {blended_roas:.2f}x (spend ₹{total_spend:,.0f})")

        if blended_roas < settings.roas_alert_threshold:
            pause_fraction = settings.adset_pause_cut_fraction
            self.emit_proposal(Proposal(
                action_type="pause_adset",
                entity_key="meta:all_campaigns",
                expected_inr_impact=total_spend * pause_fraction,
                reasoning=(
                    f"Blended ROAS is {blended_roas:.2f}x over the last 14 days "
                    f"(₹{total_spend:,.0f} spend, ₹{total_revenue:,.0f} attributed revenue). "
                    f"Pausing the bottom {pause_fraction:.0%} of campaigns by spend could save "
                    f"~₹{total_spend * pause_fraction:,.0f} while preserving higher-ROAS campaigns."
                ),
                provenance_ids=(spend_result.provenance_ids + revenue_result.provenance_ids)[:5],
                would_do_api_call={
                    "connector": "meta_ads",
                    "endpoint": "POST /{ad-set-id}",
                    "body": {"status": "PAUSED"},
                    "NOT_SENT": True,
                },
            ))
