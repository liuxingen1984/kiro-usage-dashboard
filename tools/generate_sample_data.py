"""Generate fake Kiro user_report CSV files for testing the dashboard.

Output structure mirrors the production layout:
  AWSLogs/{account}/KiroLogs/user_report/{region}/{yyyy}/{mm}/{dd}/00/{client}_{account}_user_report_{ts}.csv

Run from the project root:
  ACCOUNT_ID=111122223333 REGION=us-east-1 python3 tools/generate_sample_data.py

Use the output only in a test deployment. Do not upload it to the prefix where
Kiro delivers your real reports, or the fake users will mix with real ones.
The fake user IDs are not in IAM Identity Center, so leave IDENTITY_STORE_ID
empty when you try the dashboard with this data.
"""
import csv
import os
import random
from datetime import datetime, timedelta, timezone

ACCOUNT_ID = os.getenv("ACCOUNT_ID", "111122223333")
REGION = os.getenv("REGION", "us-east-1")
DAYS = 30           # generate 30 days of history
NUM_USERS = 8
OUT_ROOT = "sample_data"

random.seed(42)

CLIENT_TYPES = ["KIRO_IDE", "KIRO_CLI", "PLUGIN"]
# Tier spellings as they appear in the reports
TIERS = ["PRO", "PRO_PLUS", "PRO_MAX", "POWER"]

# Simulated users with stable usage profiles
USERS = []
for i in range(NUM_USERS):
    USERS.append({
        "userid": f"user-{i+1:03d}-{random.randint(1000, 9999)}",
        "tier": random.choice(TIERS),
        # Usage profile multiplier (some heavy, some light, one idle)
        "weight": [3.0, 2.5, 1.8, 1.3, 0.9, 0.5, 0.2, 0.0][i],
        "profileid": f"profile-{ACCOUNT_ID}",
    })


def overage_cap_for(tier):
    """Plan credits of the tier (what the report shows when overage is off)."""
    return {"PRO": 1000, "PRO_PLUS": 2000, "PRO_MAX": 5000, "POWER": 10000}[tier]


def generate_row(date_str, user, client):
    """Return one CSV row dict for (date, user, client). May return None if no usage."""
    # Probability the user touched this client on this day
    p_use = {"KIRO_IDE": 0.7, "KIRO_CLI": 0.4, "PLUGIN": 0.15}[client]
    if random.random() > p_use * max(user["weight"], 0.05):
        return None
    base_msgs = max(0, int(random.gauss(15, 8) * user["weight"]))
    if base_msgs == 0:
        return None
    convos = max(1, base_msgs // random.randint(3, 8))
    credits = round(base_msgs * random.uniform(0.4, 1.2), 2)
    overage_enabled = random.choice(["true", "false"])
    # As in the real reports, overage credits are part of credits_used.
    overage_used = round(credits * random.uniform(0.0, 0.3), 2) if overage_enabled == "true" else 0.0
    return {
        "date": date_str,
        "userid": user["userid"],
        "client_type": client,
        "chat_conversations": convos,
        "credits_used": credits,
        "overage_cap": overage_cap_for(user["tier"]),
        "overage_credits_used": overage_used,
        "overage_enabled": overage_enabled,
        "profileid": user["profileid"],
        "subscription_tier": user["tier"],
        "total_messages": base_msgs,
    }


COLUMNS = [
    "date", "userid", "client_type", "chat_conversations", "credits_used",
    "overage_cap", "overage_credits_used", "overage_enabled",
    "profileid", "subscription_tier", "total_messages",
]


def main():
    today = datetime.now(timezone.utc).date()
    files_written = 0

    for d in range(DAYS):
        date = today - timedelta(days=DAYS - d)
        date_str = date.strftime("%Y-%m-%d")
        for client in CLIENT_TYPES:
            rows = []
            for user in USERS:
                row = generate_row(date_str, user, client)
                if row:
                    rows.append(row)
            if not rows:
                continue
            ts = int(datetime.combine(date, datetime.min.time()).timestamp() * 1000)
            relpath = (
                f"AWSLogs/{ACCOUNT_ID}/KiroLogs/user_report/{REGION}/"
                f"{date.strftime('%Y/%m/%d')}/00/"
                f"{client}_{ACCOUNT_ID}_user_report_{ts}.csv"
            )
            outpath = os.path.join(OUT_ROOT, relpath)
            os.makedirs(os.path.dirname(outpath), exist_ok=True)
            with open(outpath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
            files_written += 1

    print(f"Wrote {files_written} CSV files under ./{OUT_ROOT}/")
    print(f"Top-level prefix to upload: {OUT_ROOT}/AWSLogs/")


if __name__ == "__main__":
    main()
