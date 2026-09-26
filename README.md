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
Stock follows the 8-week rule (owner-set 2026-09-25, same as MOAT): keep 8 weeks of inventory at Amazon on the past 30 days of sales, with tent-pole lift.
CRITICAL: FBA available 0, nothing inbound, sales in the last 30 days; or days of cover under lead time. Also policy violation, A-to-Z claim, listing removal, account at risk.
URGENT: under 56 days (8 weeks) of cover; out of stock with nothing inbound and no sales; negative seller feedback at 15%+ of 5+ ratings.
WATCH: 56 to 69 days of cover; 10+ unfulfillable units; any negative feedback. 70+ days is healthy and hidden.

Days of cover = FBA available / velocity. Velocity = (FBA + FBM units in the last 30 complete days) / 30, from Helium10 `h10_velocity_all.json`, on any ASIN with an FBA SKU (FBM-only ASINs out of scope). Scale Insights units are the fallback when H10 has no row. Every ASIN with FBA sales in the window is in scope; no H10 inventory row = 0 available, 0 inbound.
Inbound = Helium10 inbound working + shipped + receiving. Scale Insights' inbound field is not used.
Lead times: US (CC, CL, PP) 5 days, CA 14, UK/EU/AE/SA/AU 45 (owner-confirmed 2026-09-25).

DE, FR, IT, ES, NL share one FBA pool. An ASIN is treated as pooled only when every market in the pool reports the same available count. Pooled days of cover = shared stock divided by the summed velocity, and the item appears once in the action list (EU AUTO). Healthy rows are hidden by default.

Restock quantity: Amazon's FBA restock recommendation when `restock_recs.csv` is present for the week; otherwise velocity x (56 + lead time) x lift minus available and inbound, rounded up, labelled "est.".
Event lift (Cerakote Auto only; Legacy and Prismatic = 1.0): when Prime Big Deal Days, Black Friday or Cyber Monday starts inside the next 56 days, lift = last year's event-week units / average of the 3 weeks before, per ASIN, floor 1.0, from `event_history.csv`. No clean history (a zero base week, base under 7 units/week, or a zero event week) = the CC brand median of measured lifts.

## One-time setup
1. Done: repo High-Shot/INTL, Pages on main. The Monday task pushes from the Mac with gh; no token stored.
2. Seller Central, each region: Settings, Notification Preferences, add barcus@high-shot.com to Account Health, Claims, Listing notifications, Compliance.
3. Optional: drop Seller Central Restock Inventory downloads into `inbox/` as `restock_<CC>_<date>.csv`; the Monday run picks them up.

## Local rebuild
```
python3 scripts/normalize.py 2026-W36
python3 scripts/build.py
```
