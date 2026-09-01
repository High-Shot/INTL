# Cerakote INTL Health Tracker

Weekly account-health and FBA stock monitor for Cerakote's Amazon marketplaces (US, CA, UK, DE, FR, IT, ES, NL, SE, SA, AU).
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

Inbound = Helium10 inbound working + shipped + receiving. Scale Insights' inbound field is not used (it reads 0 even when H10 shows thousands of units in transit).

DE, FR, IT, ES, NL, SE share one FBA pool. Their days of cover is pooled stock divided by the sum of the six markets' velocity, and pooled items appear once in the action list under "EU". UK, CA, SA, AU, US are separate pools.

Restock quantity: Amazon's FBA restock recommendation when `restock_recs.csv` is present for the week; otherwise an estimate to reach 60 days of cover, labelled "est.".

## One-time setup
1. Create the empty GitHub repo `high-shot/INTL`, push this folder to `main`, enable Pages (Settings, Pages, deploy from branch main, root).
2. Create a fine-grained PAT scoped to that repo with Contents read/write. Save it as `.secrets/github_token` inside this folder on the Mac (gitignored).
3. Seller Central, each region: Settings, Notification Preferences, add barcus@high-shot.com to Account Health, Claims, Listing notifications, Compliance.
4. Optional: drop Seller Central Restock Inventory downloads into `inbox/` as `restock_<CC>_<date>.csv`; the Monday run picks them up.

## Local rebuild
```
python3 scripts/normalize.py 2026-W36
python3 scripts/build.py
```
