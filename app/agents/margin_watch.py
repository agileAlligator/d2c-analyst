"""Margin Watch agent — scans SKU-level contribution margin and proposes ₹-saving actions."""
import logging
from decimal import Decimal

from app.agents.base import BaseAgent, Proposal
from app.warehouse.metrics.catalog import query_metric

logger = logging.getLogger(__name__)

MARGIN_FLOOR = Decimal("0")         # flag SKUs with margin < 0
DECAY_THRESHOLD = 0.20              # flag SKUs where margin dropped >20% WoW


class MarginWatchAgent(BaseAgent):
    name = "margin_watch"

    def _execute(self):
        self.log("Starting margin scan for last 14 days...")

        current = query_metric(self.db, self.merchant_id, "contribution_margin", time_range="14d")
        self.log(f"Found {len(current.rows)} orders in last 14 days.")

        flagged = []
        for row in current.rows:
            order_id = row.get("order_number") or row.get("order_id")
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
                self._proposals.append(Proposal(
                    action_type="raise_price",
                    entity_key=f"order:{item['order_id']}",
                    expected_inr_impact=impact,
                    reasoning=(
                        f"Order {item['order_id']} has contribution margin of "
                        f"₹{margin:,.2f} (revenue ₹{revenue:,.2f}, "
                        f"shipping ₹{shipping:,.2f}, RTO cost ₹{rto:,.2f}). "
                        f"Raising the product price by {impact / max(rev_f, 1) * 100:.1f}% "
                        f"would move this order to breakeven."
                    ),
                    provenance_ids=item["prov_ids"][:5],
                    would_do_api_call={
                        "connector": "shopify",
                        "endpoint": "PUT /admin/api/2024-01/variants/{variant_id}.json",
                        "body": {"variant": {"price": str(revenue + abs(margin))}},
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
            [r for r in result.rows if r.get("total_shipments", 0) >= 5],
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

        # Estimate impact: each RTO costs ~₹150 in reverse logistics + lost COD
        est_impact = rto_count * 150.0

        self.log(f"Courier '{courier}' has RTO rate {rto_rate:.1%} ({rto_count} RTOs).")

        self._proposals.append(Proposal(
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

        if blended_roas < 2.0:
            self._proposals.append(Proposal(
                action_type="pause_adset",
                entity_key="meta:all_campaigns",
                expected_inr_impact=total_spend * 0.3,  # rough: cutting 30% of spend at low ROAS
                reasoning=(
                    f"Blended ROAS is {blended_roas:.2f}x over the last 14 days "
                    f"(₹{total_spend:,.0f} spend, ₹{total_revenue:,.0f} attributed revenue). "
                    f"Pausing the bottom 30% of campaigns by spend could save "
                    f"~₹{total_spend * 0.3:,.0f} while preserving higher-ROAS campaigns."
                ),
                provenance_ids=(spend_result.provenance_ids + revenue_result.provenance_ids)[:5],
                would_do_api_call={
                    "connector": "meta_ads",
                    "endpoint": "POST /{ad-set-id}",
                    "body": {"status": "PAUSED"},
                    "NOT_SENT": True,
                },
            ))
