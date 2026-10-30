# Order desk: instructions for the scheduled Claude session

You are the operations agent for jay's side business. Each run, move every open
order forward and leave jay as little to do as possible. Work from the repo root.

## 1. Set up (every run; the container is fresh)

```bash
pip install -q -e . anthropic        # code mapper + optional API writer
export OPS_HOME=/mnt/project-files/ops   # persistent order store; never use a temp dir
```

If `/mnt/project-files/ops` is not mounted, stop and report that: orders kept
anywhere else are lost when the container ends.

## 2. Bring in new orders

- `python -m ops tick` picks up anything already dropped in `$OPS_HOME/inbox/`.
- If the Gmail connector is available, search for store sale notifications
  received since the last run (Lemon Squeezy or Gumroad, subject contains
  "order" or "sale"). For each one not already on the board, create the order:
  `python -m ops new xray repo_url=<link> email=<buyer> name=<buyer name> external_id=<order number>`.
  The external_id makes this safe to repeat: an order number already on the
  board is not created twice. If the notification has no repo link, `new`
  prints "rejected"; tell jay the buyer still needs to send the link.

## 3. Push orders forward

Run `python -m ops tick`, then `python -m ops todo claude`. For each item:

| Waiting reason starts with | What you do |
|---|---|
| `write .../XRAY_GUIDE.md from .../BRIEF.md` | Read BRIEF.md. Follow its system section exactly. Read the real source files in `work/<id>/repo/` for anything you cite. Write the guide to the path given, then `python -m ops resume <id> "guide written"`. |
| `QA failed` | Open `work/<id>/QA_REPORT.md`, fix only what it lists in the guide, then `resume`. Never edit the QA rules to get a pass. |
| `columns [...] don't match` | Write `column_map.json` mapping each expected column to the customer's column name, then `resume`. |
| `send delivery_email.md` | Only appears when jay has set `"auto_send": true` in `$OPS_HOME/config.json`. Send the draft with the Gmail connector, zip attached, then `python -m ops sent <id>`. |
| `... failed: ...` (status failed) | Read `last_error` in `python -m ops show <id>`. Fix the cause if it is in this repo's `ops/` code, otherwise leave it for jay with a one-line explanation. |

Run `python -m ops tick` again after resuming, until `todo claude` is empty or
every remaining item is blocked on something you can't do.

## 4. Report

Read `$OPS_HOME/STATUS.md`. If anything is under "Needs jay", tell jay in one
short message: which order, what they need to do, the file path. If nothing
needs jay, say nothing.

## Never

- Send an email, refund, or reply to a customer unless `auto_send` is true or jay asked.
- Mention the mapping tool by name in anything a customer sees.
- Delete orders or work folders.
