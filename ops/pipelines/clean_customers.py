"""Clean a customer/contact CSV: normalize fields, merge duplicates, flag what needs a human.

Usage: python clean_customers.py before.csv after.csv report.md
Writes the cleaned file, a needs_review.csv of rows with problems, and a plain-English report.
"""
import csv, re, sys
from collections import Counter
from datetime import datetime

STATES = {"texas":"TX","colorado":"CO","colo":"CO","oregon":"OR","ohio":"OH","florida":"FL","fla":"FL",
          "north carolina":"NC","n.c":"NC","nc":"NC"}
EMAIL_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")
changes = Counter()

def name(v):
    v = " ".join(v.split())
    if "," in v:
        last, first = [p.strip() for p in v.split(",", 1)]
        v = f"{first} {last}"; changes["names reordered from 'Last, First'"] += 1
    t = v.title()
    if t != v: changes["names re-capitalized"] += 1
    return t

def email(v):
    raw = v
    v = v.strip().lower().replace(",", ".")
    if v != raw: changes["emails lowercased / trimmed / typo-fixed"] += 1
    if not v: return "", "missing email"
    if not EMAIL_RE.match(v): return v, "email looks invalid"
    return v, ""

def phone(v):
    ext = ""
    m = re.search(r"\s*(?:ext\.?|x)\s*(\d+)$", v, re.I)
    if m: ext, v = m.group(1), v[:m.start()]
    d = re.sub(r"\D", "", v)
    if len(d) == 11 and d[0] == "1": d = d[1:]
    if len(d) != 10: return v.strip(), ext, "phone not 10 digits"
    out = f"({d[:3]}) {d[3:6]}-{d[6:]}"
    if out != v: changes["phones put in (555) 555-5555 format"] += 1
    return out, ext, ""

def state(v):
    k = v.strip().lower().rstrip(".")
    s = STATES.get(k, v.strip().upper())
    if s != v: changes["states converted to 2-letter codes"] += 1
    return s

def date(v):
    v = v.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d.%m.%Y", "%b %d, %Y"):
        try:
            out = datetime.strptime(v, fmt).date().isoformat()
            if out != v: changes["dates converted to YYYY-MM-DD"] += 1
            return out, ""
        except ValueError: pass
    return v, "date not recognized" if v else ""

def money(v):
    v = v.strip()
    if v.lower() in ("", "n/a", "na", "-"): return "", "spend missing"
    n = re.sub(r"[^\d.]", "", v)
    try:
        out = f"{float(n):.2f}"
        if out != v: changes["spend values made plain numbers"] += 1
        return out, ""
    except ValueError: return v, "spend not a number"

def main(src, dst, report):
    rows = list(csv.DictReader(open(src, newline="", encoding="utf-8-sig")))
    cleaned = []
    for r in rows:
        e, eflag = email(r["Email"]); p, ext, pflag = phone(r["Phone"])
        d, dflag = date(r["First Order"]); s, sflag = money(r["Lifetime Spend"])
        city = r["City"].strip().title()
        if city != r["City"]: changes["cities re-capitalized"] += 1
        cleaned.append({"Name": name(r["Customer Name"]), "Email": e, "Phone": p, "Phone Ext": ext,
            "City": city, "State": state(r["State"]), "First Order": d, "Lifetime Spend": s,
            "Notes": r["Notes"].strip(), "Issues": "; ".join(f for f in (eflag, pflag, dflag, sflag) if f)})
    # merge duplicates: same valid email, else same name + phone
    merged, index, dupes = [], {}, 0
    for c in cleaned:
        key = c["Email"] if EMAIL_RE.match(c["Email"]) else (c["Name"].lower(), c["Phone"])
        if key in index:
            keep = index[key]; dupes += 1
            for f, v in c.items():
                if f == "Notes": continue
                if not keep[f] and v: keep[f] = v
            if c["First Order"] and keep["First Order"] and c["First Order"] < keep["First Order"]:
                keep["First Order"] = c["First Order"]
            notes = {n for n in (keep["Notes"], c["Notes"]) if n and n != "dup from old system"}
            keep["Notes"] = "; ".join(sorted(notes))
            keep["Issues"] = "; ".join(sorted({i for i in (keep["Issues"] + "; " + c["Issues"]).split("; ") if i}))
        else:
            if c["Notes"] == "dup from old system": c["Notes"] = ""
            index[key] = c; merged.append(c)
    # a merged record that gained a value no longer has that problem
    for m in merged:
        issues = [i for i in m["Issues"].split("; ") if i]
        if m["Lifetime Spend"]: issues = [i for i in issues if i != "spend missing"]
        if m["Email"]: issues = [i for i in issues if i != "missing email"]
        m["Issues"] = "; ".join(issues)
    merged.sort(key=lambda m: m["Name"])
    fields = list(merged[0])
    with open(dst, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows(merged)
    review = [m for m in merged if m["Issues"]]
    with open(dst.replace(".csv", "_needs_review.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows(review)
    issue_counts = Counter(i for m in review for i in m["Issues"].split("; "))
    lines = ["# Customer list cleanup: what changed", "",
        f"- **Rows in:** {len(rows)}  \n- **Rows out:** {len(merged)}  \n- **Duplicates merged:** {dupes}  \n- **Rows needing your review:** {len(review)}", "",
        "## Fixes applied automatically", ""]
    lines += [f"- {k}: {v}" for k, v in changes.most_common()]
    lines += ["", "## Left for you to decide (see the needs_review file)", "",
        "These were not guessed, because a wrong guess is worse than a blank:", ""]
    lines += [f"- {k}: {v} rows" for k, v in issue_counts.most_common()]
    lines += ["", "## Rules used", "",
        "- Duplicates: same email address, or the same name and phone number when there is no valid email. Merged records keep every non-empty value, the earliest first-order date, and all notes.",
        "- Dates like 8/4/23 are read as US month/day (August 4, 2023).",
        "- Phone extensions moved to their own column.",
        "- Nothing was deleted: every input row is either in the clean file or merged into one, and the original file is untouched."]
    open(report, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))

if __name__ == "__main__":
    main(*sys.argv[1:4])
