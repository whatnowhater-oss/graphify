# ops: order processing for the side-business products

Every product runs through the same five-part pattern:

```
intake ──► work ──► QA ──► package ──► deliver ──► status board
  ▲          │        │                    │
  │       waits on Claude (writing,     waits on jay (approve
store /    fixing QA)                   the email) or Claude
inbox                                   (send it, with auto_send)
```

An order is a JSON file in `$OPS_HOME/orders/`. `python -m ops tick` moves every
open order forward until it is done, fails, or has to wait for someone. Waits say
exactly who (`claude` or `jay`) and what to do. Crashed stages retry three times,
then show as failed on the board. Tick is safe to run as often as you like.

## Products

| Product | Stages | Agent work |
|---|---|---|
| `xray`: Codebase X-ray, $199 | intake, fetch, map, write_guide, qa, package, deliver | The code map needs no LLM. The guide is written by Claude: through the API if `ANTHROPIC_API_KEY` is set, otherwise by a Claude session following `BRIEF.md`. QA rejects any guide that cites a file not in the repo, misses a section, or leaks a secret. |
| `cleanup`: list cleanup, $249 on Fiverr | intake, map_columns, clean, qa, package, deliver | Deterministic cleaner for .csv or .xlsx up to 10,000 rows (Excel in, Excel out). If the buyer's columns are named differently, Claude writes a column map instead of guessing. QA checks no rows were lost. Delivered on the Fiverr order page; every copy of the buyer's file is deleted 30 days after delivery. |
| `kit`: Repo Kit, $19 / $49 / $149 | record | Stripe's checkout redirect delivers the download, so the order is just logged and counted. |

Add a product by writing `ops/pipelines/<name>.py` with a `PIPELINE` and listing it
in `ops/pipelines/__init__.py`. `package_stage`, `deliver_stage` and
`writer_stage` in `common.py` are reusable as-is.

## Commands

```bash
export OPS_HOME=/mnt/project-files/ops      # where orders live (default ./ops-data)
python -m ops new xray repo_url=https://github.com/org/repo email=buyer@x.com name=Sam
python -m ops webhook lemonsqueezy payload.json   # or gumroad
python -m ops tick                          # do all the work that can be done now
python -m ops todo jay                      # what's waiting on jay
python -m ops resume <id>                   # after fixing what a wait asked for
python -m ops sent <id>                     # delivery email went out; order done
python -m ops board                         # STATUS.md (status.html is written too)
pytest ops/tests                            # offline end-to-end tests
```

## How orders get in

1. **Payment emails (the default).** The scheduled Claude session (see
   `ROUTINE.md`) reads Stripe payment emails with the Gmail connector and creates
   orders. Nothing to set up beyond the Stripe Payment Link.
2. **Webhook (optional, faster).** A Stripe `checkout.session.completed` webhook
   (or Lemon Squeezy `order_created` / Gumroad ping) saved into `$OPS_HOME/inbox/`
   as `{"provider": "stripe", "payload": {...}}`. Duplicate deliveries of the same
   webhook don't create duplicate orders. Set metadata `product=xray` on the
   Payment Link; a checkout field whose label mentions "GitHub URL" becomes the repo.
3. **By hand.** `python -m ops new ...`.

The board's goal line counts what's kept after fees (Fiverr 20%, Stripe about 3%),
because that's what reaches jay.

Fiverr orders come in by hand or from the Fiverr notification email:
`ops new cleanup file_path=$OPS_HOME/uploads/<order>.xlsx channel=fiverr external_id=fiverr:<order> name=<buyer>`.

X-ray takes public GitHub repositories only; an order without a public link is
rejected so the buyer can be asked for one. Customer data is deleted after each product's promised
retention period (`ops purge`): 7 days for X-ray code, 30 days for cleanup files.

## What needs jay, honestly

- The Stripe Payment Link, already on the launch list.
- Approving each delivery email, until jay sets `"auto_send": true` in
  `$OPS_HOME/config.json`. After that Claude sends them.
- Optional: an Anthropic API key so guides get written the moment an order
  lands, instead of on the next scheduled Claude run.
- Refunds, disputes and unhappy buyers (escalated per `xray/OPERATIONS.md`).
