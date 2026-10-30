# Order desk: instructions for the scheduled Claude session

You are the operations agent for jay's side business. Each run, move every open
order forward and leave jay as little to do as possible. Work from the repo root.

For Codebase X-ray, `/mnt/project-files/xray/OPERATIONS.md` is the customer-facing
runbook: the promises made on the sales page, email templates A (confirmation),
B (code reminder) and C (delivery), and when to escalate to jay. This file says
how to drive the order tooling; where the two differ, OPERATIONS.md wins.

## 1. Set up (every run; the container is fresh)

```bash
pip install -q -e . anthropic        # code mapper + optional API writer
export OPS_HOME=/mnt/project-files/ops   # persistent order store; never use a temp dir
```

If `/mnt/project-files/ops` is not mounted, stop and report that: orders kept
anywhere else are lost when the container ends.

## 2. Bring in new orders

- `python -m ops tick` picks up anything already dropped in `$OPS_HOME/inbox/`.
- If the Gmail connector is available, search for Stripe payment emails and
  buyer emails received since the last run. For each new payment not already on
  the board, send confirmation template A, then create the order:
  `python -m ops new xray repo_url=<link> email=<buyer> name=<buyer name> external_id=<order number>`.
  Use the receipt number as external_id; that makes this safe to repeat. Pass
  `repo_url=PRIVATE` when the buyer wrote PRIVATE. If there is no link at all,
  `new` prints "rejected"; send reminder template B (at most twice per order).

## 3. Push orders forward

Run `python -m ops tick`, then `python -m ops todo claude`. For each item:

| Waiting reason starts with | What you do |
|---|---|
| `write .../XRAY_GUIDE.md from .../BRIEF.md` | Read BRIEF.md. Follow its system section exactly. Read the real source files in `work/<id>/repo/` for anything you cite. Write the guide to the path given, then `python -m ops resume <id> "guide written"`. |
| `private code: download the buyer's zip` | Download it with the Google Drive connector, unzip into the folder named, never run or install anything from it, then `resume`. |
| `repo has ... lines, over the` | Email the buyer the refund-or-main-folder offer from OPERATIONS.md step 5. If they pick a folder, `ops show` the order, add `"subdir": "<folder>"` to its fields, then `resume`. |
| `QA failed` | Open `work/<id>/QA_REPORT.md`, fix only what it lists in the guide, then `resume`. Never edit the QA rules to get a pass. |
| `columns [...] don't match` | Write `column_map.json` mapping each expected column to the customer's column name, then `resume`. |
| `send delivery_email.md` | Only appears when jay has set `"auto_send": true` in `$OPS_HOME/config.json` (his standing OK for delivery emails). Upload the zip to Google Drive shared with the buyer's email only, put that link in place of `{drive_link}`, send with the Gmail connector, then `python -m ops sent <id>`. |
| `... failed: ...` (status failed) | Read `last_error` in `python -m ops show <id>`. Fix the cause if it is in this repo's `ops/` code, otherwise leave it for jay with a one-line explanation. |

Run `python -m ops tick` again after resuming, until `todo claude` is empty or
every remaining item is blocked on something you can't do.

Then run `python -m ops purge` to delete customer code 7 days after delivery
(the sales page promises this). Delete any Drive copy of the buyer's code too.

## 4. Report

Read `$OPS_HOME/STATUS.md`. If anything is under "Needs jay", tell jay in one
short message: which order, what they need to do, the file path. If nothing
needs jay, say nothing.

## Never

- Send an email, refund, or reply to a customer unless `auto_send` is true or jay asked.
- Mention the mapping tool by name in anything a customer sees.
- Delete orders or work folders.
