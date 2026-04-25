"""
Ledger — Gmail Expense Agent
------------------------------
Scans your Gmail for receipts and extracts expense data using
a local Ollama model (qwen2.5:14b), then generates an interactive
HTML report with charts.

Requirements:
    pip install openai google-auth google-auth-oauthlib google-api-python-client

Setup:
    1. Make sure Ollama is running:
           ollama serve
    2. Make sure qwen2.5:14b is pulled:
           ollama pull qwen2.5:14b
    3. Go to https://console.cloud.google.com
    4. Create a project → Enable Gmail API
    5. Create OAuth 2.0 credentials (Desktop app) → Download as credentials.json
    6. Place credentials.json in the same folder as this script
    7. Run:
           python ledger_agent.py
"""

import os
import json
import base64
import time
import re
from datetime import datetime
from collections import defaultdict, Counter

# ── CONFIG ────────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL      = "http://localhost:11434/v1"
OLLAMA_MODEL         = "qwen2.5:14b"
CREDENTIALS_FILE     = "credentials.json"
TOKEN_FILE           = "gmail_token.json"
OUTPUT_HTML          = "ledger_report.html"
MAX_EMAILS_PER_QUERY = 50       # raise to 100+ for deeper scans (slower)
SLEEP_BETWEEN_CALLS  = 0.2      # seconds between Ollama calls
# ─────────────────────────────────────────────────────────────────────────────

from openai import OpenAI

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

SEARCH_QUERIES = [
    "receipt OR invoice OR 'order confirmation'",
    "'amount charged' OR 'payment successful' OR transaction",
    "subscription OR 'your plan' OR renewal",
    "Zomato OR Swiggy OR 'food delivery'",
    "Amazon OR Flipkart OR 'your order'",
    "Uber OR Ola OR 'booking confirmed' OR flight OR hotel",
]

CAT_COLORS = {
    "Food & Dining": "#E24B4A",
    "Shopping":      "#185FA5",
    "Travel":        "#1D9E75",
    "Subscriptions": "#7F77DD",
    "Other":         "#888780",
}
CAT_BG = {
    "Food & Dining": "#FCEBEB",
    "Shopping":      "#E6F1FB",
    "Travel":        "#E1F5EE",
    "Subscriptions": "#EEEDFE",
    "Other":         "#F1EFE8",
}


# ── OLLAMA ────────────────────────────────────────────────────────────────────

def get_ollama_client() -> OpenAI:
    return OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")


def check_ollama():
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3) as r:
            data   = json.loads(r.read())
            models = [m["name"] for m in data.get("models", [])]
            match  = [m for m in models if OLLAMA_MODEL.split(":")[0] in m]
            if not match:
                print(f"  ✗  Model '{OLLAMA_MODEL}' not found.")
                print(f"     Run:  ollama pull {OLLAMA_MODEL}")
                raise SystemExit(1)
            print(f"  ✓  Ollama running  ·  model: {match[0]}")
    except OSError:
        print("  ✗  Ollama is not running.")
        print("     Start it with:  ollama serve")
        raise SystemExit(1)


# ── GMAIL AUTH ────────────────────────────────────────────────────────────────

def get_gmail_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"\n  ✗  '{CREDENTIALS_FILE}' not found.")
                print("     Download OAuth2 Desktop credentials from:")
                print("     https://console.cloud.google.com → APIs & Services → Credentials")
                print("     Save the file as credentials.json in this folder.\n")
                raise SystemExit(1)
            flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


# ── EMAIL FETCHING ────────────────────────────────────────────────────────────

def search_emails(service, query: str, max_results: int = 50) -> list[dict]:
    results    = []
    page_token = None
    fetched    = 0

    while fetched < max_results:
        batch  = min(max_results - fetched, 100)
        kwargs = {"userId": "me", "q": query, "maxResults": batch}
        if page_token:
            kwargs["pageToken"] = page_token

        resp     = service.users().messages().list(**kwargs).execute()
        messages = resp.get("messages", [])
        if not messages:
            break

        for msg in messages:
            meta = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["Subject", "From", "Date"]
            ).execute()
            headers = {h["name"]: h["value"] for h in meta.get("payload", {}).get("headers", [])}
            results.append({
                "id":      msg["id"],
                "subject": headers.get("Subject", ""),
                "from":    headers.get("From", ""),
                "date":    headers.get("Date", ""),
            })
            fetched += 1
            if fetched >= max_results:
                break

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    return results


def get_email_body(service, msg_id: str) -> str:
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    def extract(payload):
        body = ""
        if "parts" in payload:
            for part in payload["parts"]:
                body += extract(part)
        else:
            mime = payload.get("mimeType", "")
            if mime in ("text/plain", "text/html"):
                data = payload.get("body", {}).get("data", "")
                if data:
                    decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                    if mime == "text/html":
                        decoded = re.sub(r"<[^>]+>", " ", decoded)
                        decoded = re.sub(r"\s+", " ", decoded)
                    body += decoded
        return body

    body = extract(msg.get("payload", {}))
    return body.strip()[:2500]  # truncate — keeps prompts fast


# ── EXPENSE EXTRACTION ────────────────────────────────────────────────────────

PROMPT = """You are an expense extraction assistant. Read the email and extract the transaction.

Subject: {subject}
From: {sender}
Body:
{body}

Return ONLY a valid JSON object. No markdown, no explanation, nothing else.

If a clear payment or purchase exists:
{{
  "found": true,
  "amount": <positive number in INR>,
  "merchant": "<brand name>",
  "date": "<YYYY-MM-DD>",
  "category": "<Food & Dining | Shopping | Travel | Subscriptions | Other>",
  "description": "<one short line>"
}}

If no clear transaction exists:
{{"found": false}}

Rules:
- amount is a number only, no currency symbols
- Convert non-INR to approximate INR if needed
- merchant is the brand name, not the email domain
- date must be YYYY-MM-DD"""


def extract_expense(client: OpenAI, subject: str, sender: str, body: str) -> dict | None:
    prompt = PROMPT.format(subject=subject, sender=sender, body=body)

    try:
        resp = client.chat.completions.create(
            model=OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,      # deterministic = more reliable JSON
            max_tokens=300,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"```json|```", "", raw).strip()

        # grab first JSON object in case model adds extra text
        m = re.search(r"\{[\s\S]*?\}", raw)
        if not m:
            return None

        data = json.loads(m.group())

        if not data.get("found"):
            return None

        amount = float(data.get("amount", 0))
        if amount <= 0:
            return None

        date_str = str(data.get("date", ""))
        if not re.match(r"\d{4}-\d{2}-\d{2}", date_str):
            date_str = ""

        return {
            "merchant":    str(data.get("merchant", sender))[:60],
            "amount":      round(amount, 2),
            "date":        date_str,
            "category":    data.get("category", "Other"),
            "description": str(data.get("description", subject))[:100],
        }

    except (json.JSONDecodeError, ValueError, KeyError):
        return None


# ── DEDUPLICATION ─────────────────────────────────────────────────────────────

def deduplicate(txs: list[dict]) -> list[dict]:
    seen, unique = set(), []
    for t in txs:
        key = (t["merchant"].lower()[:20], round(t["amount"]), t["date"])
        if key not in seen:
            seen.add(key)
            unique.append(t)
    return unique


# ── HTML REPORT ───────────────────────────────────────────────────────────────

def generate_report(txs: list[dict], path: str):
    monthly = defaultdict(float)
    for t in txs:
        if t["date"]:
            monthly[t["date"][:7]] += t["amount"]
    monthly_sorted = sorted(monthly.items())
    month_labels   = [k for k, _ in monthly_sorted]
    month_values   = [round(v, 2) for _, v in monthly_sorted]

    cat_totals = defaultdict(float)
    for t in txs:
        cat_totals[t["category"]] += t["amount"]

    total     = round(sum(t["amount"] for t in txs), 2)
    avg_month = round(total / max(len(monthly), 1), 2)
    top_cat   = max(cat_totals, key=cat_totals.get) if cat_totals else "—"
    tx_sorted = sorted(txs, key=lambda x: x["date"], reverse=True)

    rows = ""
    for t in tx_sorted:
        color = CAT_COLORS.get(t["category"], "#888780")
        bg    = CAT_BG.get(t["category"], "#F1EFE8")
        rows += f"""
          <tr>
            <td class="mono muted">{t['date'] or '—'}</td>
            <td>{t['merchant']}</td>
            <td><span class="badge" style="background:{bg};color:{color}">{t['category']}</span></td>
            <td class="mono right">₹{t['amount']:,.0f}</td>
          </tr>"""

    pie_labels = list(cat_totals.keys())
    pie_values = [round(v, 2) for v in cat_totals.values()]
    pie_colors = [CAT_COLORS.get(c, "#888780") for c in pie_labels]

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ledger</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',system-ui,sans-serif;background:#f5f4f0;color:#1a1a1a}}
  .wrap{{max-width:940px;margin:0 auto;padding:2rem 1rem 4rem}}
  .logo{{font-size:24px;font-weight:600;letter-spacing:-0.5px;margin-bottom:2px}}
  .logo span{{color:#185FA5}}
  .sub{{font-size:12px;color:#999;font-family:monospace;margin-bottom:1.75rem}}
  .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:1.25rem}}
  .metric{{background:#eceae4;border-radius:10px;padding:14px 16px}}
  .metric-label{{font-size:10px;color:#888;text-transform:uppercase;letter-spacing:0.6px;font-family:monospace;margin-bottom:5px}}
  .metric-value{{font-size:26px;font-weight:500;letter-spacing:-0.5px}}
  .metric-sub{{font-size:11px;color:#888;margin-top:3px}}
  .charts-row{{display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem}}
  @media(max-width:600px){{.charts-row{{grid-template-columns:1fr}}}}
  .card{{background:#fff;border:0.5px solid #e0dfd8;border-radius:12px;padding:1.25rem;margin-bottom:1rem}}
  .card-title{{font-size:10px;font-family:monospace;text-transform:uppercase;letter-spacing:0.6px;color:#bbb;margin-bottom:1rem}}
  .tab-row{{display:flex;gap:6px;margin-bottom:1rem;flex-wrap:wrap}}
  .tab{{padding:4px 12px;font-size:12px;border-radius:20px;border:0.5px solid #ccc;background:none;cursor:pointer;font-family:inherit;transition:all .15s}}
  .tab:hover{{background:#f0efe8}}
  .tab.active{{background:#185FA5;color:#fff;border-color:#185FA5}}
  table{{width:100%;border-collapse:collapse;font-size:13px}}
  th{{text-align:left;font-size:10px;font-family:monospace;color:#aaa;padding:6px 8px;border-bottom:0.5px solid #e8e7e0;font-weight:400;text-transform:uppercase}}
  td{{padding:9px 8px;border-bottom:0.5px solid #f0efe8;vertical-align:middle}}
  tr:last-child td{{border-bottom:none}}
  tr:hover td{{background:#faf9f6}}
  .mono{{font-family:monospace}}
  .muted{{color:#999;font-size:12px}}
  .right{{text-align:right;font-weight:500}}
  .badge{{padding:2px 9px;border-radius:10px;font-size:11px;font-family:monospace;white-space:nowrap}}
  .legend{{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px;font-size:12px;color:#666}}
  .dot{{width:10px;height:10px;border-radius:2px;display:inline-block;margin-right:4px;vertical-align:middle}}
  .footer{{font-size:11px;color:#ccc;text-align:center;margin-top:2rem;font-family:monospace}}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">Led<span>ger</span></div>
  <div class="sub">generated {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;·&nbsp; all time &nbsp;·&nbsp; {len(txs)} transactions &nbsp;·&nbsp; qwen2.5:14b</div>

  <div class="metrics">
    <div class="metric">
      <div class="metric-label">Total spent</div>
      <div class="metric-value">₹{total:,.0f}</div>
      <div class="metric-sub">{len(txs)} transactions</div>
    </div>
    <div class="metric">
      <div class="metric-label">Avg / month</div>
      <div class="metric-value">₹{avg_month:,.0f}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Top category</div>
      <div class="metric-value" style="font-size:17px;margin-top:4px">{top_cat}</div>
      <div class="metric-sub">₹{cat_totals.get(top_cat, 0):,.0f}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Months tracked</div>
      <div class="metric-value">{len(monthly)}</div>
    </div>
  </div>

  <div class="charts-row">
    <div class="card">
      <div class="card-title">by category</div>
      <div style="position:relative;height:200px">
        <canvas id="catChart" role="img" aria-label="Pie chart of spending by category"></canvas>
      </div>
      <div class="legend" id="catLegend"></div>
    </div>
    <div class="card">
      <div class="card-title">monthly trend</div>
      <div style="position:relative;height:200px">
        <canvas id="monthChart" role="img" aria-label="Bar chart of monthly spending"></canvas>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">transactions</div>
    <div class="tab-row">
      <button class="tab active" onclick="filter('all',this)">All</button>
      <button class="tab" onclick="filter('Food & Dining',this)">Food</button>
      <button class="tab" onclick="filter('Shopping',this)">Shopping</button>
      <button class="tab" onclick="filter('Travel',this)">Travel</button>
      <button class="tab" onclick="filter('Subscriptions',this)">Subscriptions</button>
      <button class="tab" onclick="filter('Other',this)">Other</button>
    </div>
    <table>
      <thead>
        <tr><th>Date</th><th>Merchant</th><th>Category</th><th style="text-align:right">Amount</th></tr>
      </thead>
      <tbody id="txBody">{rows}</tbody>
    </table>
  </div>

  <div class="footer">Ledger &nbsp;·&nbsp; powered by qwen2.5:14b via Ollama</div>
</div>

<script>
const allTx = {json.dumps(tx_sorted)};
const catColors = {json.dumps(CAT_COLORS)};
const catBg = {json.dumps(CAT_BG)};
const pieLabels = {json.dumps(pie_labels)};
const pieValues = {json.dumps(pie_values)};
const pieColors = {json.dumps(pie_colors)};

new Chart(document.getElementById('catChart'), {{
  type: 'doughnut',
  data: {{
    labels: pieLabels,
    datasets: [{{ data: pieValues, backgroundColor: pieColors, borderWidth: 2, borderColor: '#fff' }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    cutout: '60%'
  }}
}});

document.getElementById('catLegend').innerHTML = pieLabels.map((c, i) =>
  `<span><span class="dot" style="background:${{pieColors[i]}}"></span>${{c}}&nbsp;<span style="font-family:monospace">₹${{pieValues[i].toLocaleString('en-IN')}}</span></span>`
).join('');

new Chart(document.getElementById('monthChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(month_labels)},
    datasets: [{{ data: {json.dumps(month_values)}, backgroundColor: '#378ADD', borderRadius: 4, borderSkipped: false }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ grid: {{ display: false }}, ticks: {{ maxRotation: 45, autoSkip: true, maxTicksLimit: 12, font: {{ size: 10 }} }} }},
      y: {{ grid: {{ color: 'rgba(0,0,0,0.05)' }}, ticks: {{ callback: v => '₹' + v.toLocaleString('en-IN'), font: {{ size: 10 }} }} }}
    }}
  }}
}});

function filter(cat, btn) {{
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const filtered = cat === 'all' ? allTx : allTx.filter(t => t.category === cat);
  document.getElementById('txBody').innerHTML = filtered.map(t => `
    <tr>
      <td class="mono muted">${{t.date || '—'}}</td>
      <td>${{t.merchant}}</td>
      <td><span class="badge" style="background:${{catBg[t.category]||'#F1EFE8'}};color:${{catColors[t.category]||'#888'}}">${{t.category}}</span></td>
      <td class="mono right">₹${{Math.round(t.amount).toLocaleString('en-IN')}}</td>
    </tr>`).join('');
}}
</script>
</body>
</html>"""

    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  ✓  Report saved → {path}")


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 52)
    print("   Ledger  ·  Gmail Expense Agent")
    print("   model: qwen2.5:14b via Ollama")
    print("═" * 52)

    # Check Ollama is up
    print("\n[1/4] Checking Ollama…")
    check_ollama()

    # Ollama client
    client = get_ollama_client()

    # Gmail auth
    print("\n[2/4] Connecting to Gmail…")
    service = get_gmail_service()
    print("  ✓  Gmail authenticated")

    # Search emails
    print(f"\n[3/4] Searching Gmail ({len(SEARCH_QUERIES)} query passes)…")
    seen_ids   = set()
    all_emails = []

    for q in SEARCH_QUERIES:
        emails = search_emails(service, q, MAX_EMAILS_PER_QUERY)
        new    = [e for e in emails if e["id"] not in seen_ids]
        seen_ids.update(e["id"] for e in new)
        all_emails.extend(new)
        print(f"  {q[:55]:<55}  +{len(new):>3}  ({len(all_emails)} total)")

    print(f"\n  Found {len(all_emails)} unique emails to process")

    # Extract expenses
    print(f"\n[4/4] Extracting expenses with {OLLAMA_MODEL}…\n")
    transactions = []

    for i, email in enumerate(all_emails, 1):
        label = email["subject"][:60]
        print(f"  [{i:>4}/{len(all_emails)}]  {label:<60}", end="  ", flush=True)

        try:
            body   = get_email_body(service, email["id"])
            result = extract_expense(client, email["subject"], email["from"], body)

            if result:
                transactions.append(result)
                print(f"✓  ₹{result['amount']:>9,.0f}  {result['category']}")
            else:
                print("–")
        except Exception as e:
            print(f"✗  {e}")

        time.sleep(SLEEP_BETWEEN_CALLS)

    # Deduplicate
    transactions = deduplicate(transactions)

    # Generate report
    generate_report(transactions, OUTPUT_HTML)

    # Final summary
    total = sum(t["amount"] for t in transactions)
    cats  = Counter(t["category"] for t in transactions)

    print("\n" + "═" * 52)
    print(f"  Total:          ₹{total:>12,.0f}")
    print(f"  Transactions:   {len(transactions):>5}")
    print()
    for cat, count in cats.most_common():
        cat_total = sum(t["amount"] for t in transactions if t["category"] == cat)
        print(f"  {cat:<20}  {count:>3} tx   ₹{cat_total:>10,.0f}")
    print("═" * 52)
    print(f"\n  Open {OUTPUT_HTML} in your browser.\n")


if __name__ == "__main__":
    main()
