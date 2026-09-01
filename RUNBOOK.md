# INTL Health Tracker: weekly run

Runs every Monday 06:00 CT as a Cowork scheduled task. Fresh session, no memory. Everything needed is below.

Accounts covered: Cerakote Auto (CC: US, CA, UK, DE, FR, IT, ES, NL, AE, SA, AU), Cerakote Legacy (CL: US, CA, MX), Prismatic Powders (PP: US, CA, MX). Seller IDs: AOXMQPMOL1F1Y = CC US/CA; A3BMUMIXNXIR6G = CC UK/EU/AE/SA; A22UNGVVL3ZGDF = CC AU; A1KUYEQ8RRQVVI = CL; A21D21T8B6U09C = PP.

Goal: produce `data/raw/<WEEK>/*`, run normalize + build, push to GitHub, send Barcus a summary of CRITICAL and URGENT items only.

WEEK = ISO week of the run date, formatted `YYYY-Www` (example: 2026-W37). `date +%G-W%V`.

## 0. Setup (cloud shell)
```
git clone https://github.com/high-shot/INTL.git /home/claude/INTL && cd /home/claude/INTL
mkdir -p data/raw/$WEEK
```
Publishing happens FROM BARCUS'S MAC (gh is logged in there; no token is stored anywhere). See step 6.

## 1. Helium10 inventory (authoritative stock + inbound)
Call `mcp__Helium10__get_inventory_values` with
`seller_ids: ["AOXMQPMOL1F1Y","A3BMUMIXNXIR6G","A22UNGVVL3ZGDF","A1KUYEQ8RRQVVI","A21D21T8B6U09C"], fulfillment_type: "FBA", page_size: 500`.
The result overflows into a tool-results file. Copy that file verbatim to `data/raw/$WEEK/h10_inventory.json`.
If total_count > 500, call page 2 and save it as `data/raw/$WEEK/h10_inventory_2.json` (normalize merges `h10_inventory_*.json`).

## 1b. Helium10 sales velocity (all brands, one call)
Call `mcp__Helium10__get_sales_velocity` with `fulfillment_type: "FBA", granularity: "month", page_size: 500, current_date_from: <30 days ago>, current_date_to: <yesterday>` (both YYYY-MM-DD; if the window crosses a month boundary the row carries two buckets, normalize sums them).
Copy the tool-results file verbatim to `data/raw/$WEEK/h10_velocity.json`. This is the velocity source for Legacy, Prismatic and SA; Scale Insights overrides it where present (Cerakote Auto).

## 2. Scale Insights velocity (one call per market)
Scale Insights is connected to the Cerakote Auto account only. Call `mcp__Scale_Insights__get_inventory_data` with `country: <CC>, count: 100` for CC in
US, CA, UK, DE, FR, IT, ES, NL, AU. (AE and SA return no data as of 2026-09-01; try them once, skip if empty. Page 2 if has_next_page.)
Write `data/raw/$WEEK/si_inventory.csv`, one row per ASIN returned, header exactly:
```
market,asin,sku,fba,transfer,inbound,units30,vel,doc,ad30,risk,spending_low,name
```
Map: fba=CurrentFBAStock, transfer=StockInTransfer, inbound=StockInbound, units30=UnitsSoldInPeriod, vel=AvgDailyUnitSales, doc=DaysOfCoverCurrentVelocity (blank if null), ad30=AdSpendInWindow, risk=StockRisk, spending_low=StillSpendingWhileLowStock, name=short product name (only needed for ASINs that H10 does not return, e.g. US bundles; may be blank otherwise).

## 3. Seller feedback (one call per seller x market)
Call `mcp__Helium10__get_seller_feedback` with `time_window: "30d"` for:
AOXMQPMOL1F1Y x US, CA; A3BMUMIXNXIR6G x UK, DE, FR, IT, ES, NL, AE, SA; A22UNGVVL3ZGDF x AU; A1KUYEQ8RRQVVI x US, CA; A21D21T8B6U09C x US, CA.
Write `data/raw/$WEEK/seller_feedback.csv`, header:
```
seller_id,market,window,rating,count,pos_pct,neg_pct,recent_negative
```
recent_negative = the negatives joined with ` || ` as `YYYY-MM-DD: comment`. Quote the field.

## 4. Account health (Seller Central notification emails)
Search Gmail: `mcp__Gmail__search_threads` with
`query: from:amazon newer_than:8d (subject:"A-to-z" OR subject:"Account Health" OR subject:"policy" OR subject:"Action required" OR subject:"removed" OR subject:"suppressed" OR subject:"Intellectual Property" OR subject:"complaint" OR subject:"Restricted")`
Skip anything from an @amazon.com person (account managers, Business Essentials pitches). Keep only automated notifications (seller-notification, donotreply, no-reply, marketplace-messages, sellercentral senders).
For each kept message, read it with get_thread and write `data/raw/$WEEK/account_health.json`:
```
{ "UK": { "as_of": "2026-09-08", "ahr_status": null, "ahr_score": null, "listing_issues": false,
          "notifications": [ { "date": "2026-09-05", "type": "atoz_claim|policy_violation|listing_removed|ip_complaint|restricted_product|account_at_risk|other",
                               "asin": "B0XXXXXXXX" or null, "subject": "<email subject>", "severity": "CRITICAL|URGENT|WATCH", "action": "<one line>" } ] } }
```
Keys are account codes: `CC_UK`, `CC_DE`, `CL_US`, `PP_US`, etc. (a bare market code like `UK` is read as `CC_UK`). Marketplace comes from the email's domain or body; brand from the seller account named in the email. Carry forward every still-open item from last week's `account_health.json` (same `date`, `type`, `asin`) unless a resolution email arrived; add `status` (open / appeal submitted / under review / resolved) when known. If no qualifying emails, copy last week's file forward and note "no new notifications" in the summary. If Gmail returns nothing at all for 3 runs in a row, tell Barcus the Notification Preferences setup may not have taken.

## 5. Amazon restock recommendations (optional)
If `"$HOME/mnt/Cerakote Management/intl-tracker/inbox/restock_*.csv"` exists on the linked computer (Barcus drops the Seller Central Restock Inventory download there), convert it to `data/raw/$WEEK/restock_recs.csv` with header `market,asin,sku,rec_qty,rec_date` and move the source file to `inbox/processed/`. Otherwise skip; the dashboard shows estimates.

## 6. Build and publish
```
python3 scripts/normalize.py $WEEK
python3 scripts/build.py
```
Then copy the new files onto the Mac and push from there (the Mac's git already has GitHub credentials via gh):
6a. SendUserFile + device_commit_files for: index.html, data/snapshots/$WEEK.json, and every file in data/raw/$WEEK/, into the matching paths under `/Users/barcus/Documents/Claude/Projects/Cerakote Management/intl-tracker/`.
6b. mcp__remote-devices__Control_your_Mac__osascript:
```
do shell script "export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH; cd \"$HOME/Documents/Claude/Projects/Cerakote Management/intl-tracker\" && git pull -q --rebase origin main; git add -A && git commit -q -m 'Weekly snapshot $WEEK' && git push -q origin main && git log --oneline -1"
```
6c. If the Mac is unreachable: fall back to `.secrets/github_token` on the Mac if it exists (`git push https://x-access-token:$TOKEN@github.com/High-Shot/INTL.git main` from the cloud clone). If neither works, report "not published" in the summary; the files are already in the folder.

## 7. Summary message to Barcus (SendUserMessage)
Lead line: "INTL $WEEK: N critical, N urgent, N watch (Δ vs last week)". Then one line per CRITICAL and URGENT stock item: market, SKU, name, available, inbound, days of cover, ads 30d, restock qty (Amazon rec or est.). Then account items. Then one line for anything that failed (a market with no SI data, Gmail empty, push failed). Link: https://high-shot.github.io/INTL/
No other prose.

## Rules
- Never invent a number. A market with no data is reported as "no data", not zero.
- Never pause to ask a question. Make the reasonable call, flag it in the summary.
- A failed step must not stop the run. Skip it, continue, report it.
- Do not touch ads, listings, or shipments. This is read-only monitoring.

## Midweek check (Thursday 06:00 CT, separate scheduled task)
Only steps 1 and 2 for the intl markets, no build, no push. Message Barcus only if any ASIN in CA/UK/DE/FR/IT/ES/NL/AE/SA/AU is at available 0 with inbound 0 and units30 > 0, or under 7 days of cover with inbound 0. If nothing qualifies, send nothing.
