# NIC Industries Account Health Tracker (INTL)

Weekly account-health and FBA stock monitor for NIC Industries' Amazon accounts: Cerakote Auto (US, CA, UK, DE, FR, IT, ES, NL, AE, SA, AU), Cerakote Legacy (US only), Prismatic Powders (US only).
Live page: https://high-shot.github.io/INTL/

Same shape as the NIC tracker: a Python build injects weekly snapshot JSON into `template.html`, output is a static `index.html` on GitHub Pages.

```
data/raw/<WEEK>/        source pulls for the week (H10 inventory JSON, SI velocity CSV, feedback CSV, account_health.json, restock_recs.csv)
data/snapshots/<WEEK>.json   normalized snapshot, one per week (history for the diff and the chart)
scripts/normalize.py    raw -> snapshot, applies the alert rules
scripts/build.py        snapshots -> index.html
template.html           the dashboard
RUNBOOK.md              what the Monday scheduled task does, step by step
```

## Rules
CRITICAL: FBA available 0 and nothing inbound, with sales in the last 30 days. Also policy violation, A-to-Z claim, listing removal, account at risk.
URGENT: under 14 days of cover and nothing inbound; out of stock with inbound; negative seller feedback at 15%+ of 5+ ratings.
WATCH: 14 to 28 days of cover with nothing inbound; under 14 days with inbound on the way; 10+ unfulfillable units; any negative feedback.

Inbound = Helium10 inbound working + shipped + receiving. Scale Insights' inbound field is not used (it reads 0 even when H10 shows thousands of units in transit). Velocity = Scale Insights where connected (Cerakote Auto), else Helium10 get_sales_velocity (Legacy, Prismatic, SA).

DE, FR, IT, ES, NL share one FBA pool. An ASIN is treated as pooled only when every market in the pool reports the same available count. Pooled days of cover = shared stock divided by the summed velocity, and the item appears once in the action list (EU AUTO). Healthy rows are hidden by default.

Restock quantity: Amazon's FBA restock recommendation when `restock_recs.csv` is present for the week; otherwise velocity x (lead time + 30 days) minus available and inbound, labelled "est." (lead times in scripts/normalize.py POOL_LEAD).

## One-time setup
1. Done: repo High-Shot/INTL, Pages on main. The Monday task pushes from the Mac with gh; no token stored.
2. Seller Central, each region: Settings, Notification Preferences, add barcus@high-shot.com to Account Health, Claims, Listing notifications, Compliance.
3. Optional: drop Seller Central Restock Inventory downloads into `inbox/` as `restock_<CC>_<date>.csv`; the Monday run picks them up.

## Local rebuild
```
python3 scripts/normalize.py 2026-W36
python3 scripts/build.py
```
