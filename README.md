# Kiro Usage Dashboard

A dashboard for Kiro administrators. It reads Kiro's daily per-user activity reports and
shows, for each user, credits, overage, messages, conversations, clients and activity. It
runs on your own machine against your own AWS account and sends the data nowhere else.

Kiro gives each subscription a new monthly credit allowance on the first day of every
calendar month, so the dashboard opens on a **monthly view** that compares each user's
credits with their tier's allowance. People who have left your organization drop out of
the monthly view, while an **all-time view** keeps their history.

This project builds on
[aws-samples/sample-kiro-user-analytics-dashboard](https://github.com/aws-samples/sample-kiro-user-analytics-dashboard)
(MIT-0).

## Features

- **Monthly view (default).** Pick the current month or any earlier month. Every chart and
  table covers only that calendar month.
- **Credits vs. monthly allowance.** Per user: tier, monthly credits (Pro 1,000, Pro+ 2,000,
  Pro Max 5,000, Power 10,000), credits used, overage, percentage used and plan credits left.
  A mid-month upgrade counts the higher tier for the whole month, as Kiro bills it.
- **Removed users stay out of the monthly view.** Users deleted from or disabled in IAM
  Identity Center are hidden there. The page says how many were hidden and how much they
  used in that month.
- **All-time view.** Everything since reports started, including removed users (shown as
  `Removed user 1a2b3c4d`), and a user × month credit table.
- Usage by client (IDE, CLI, plugin), top users, daily trends, tier breakdown, engagement
  segments, activity timeline, and exports to HTML, Excel and CSV.

## How it works

```
Kiro ──daily CSV──▶ S3 ──▶ Glue crawler ──▶ Athena ◀── Streamlit app on your machine
                                                          └─▶ IAM Identity Center (names, removed users)
```

Terraform creates the Glue database and crawler, an Athena workgroup with a results
bucket, and a read-only IAM policy for the dashboard. The report bucket and the Kiro
report setting are yours; the steps below set them up if needed.

## Deploy with an AI agent

[AGENTS.md](AGENTS.md) is a deployment runbook written for coding agents. Open this
repository in Kiro (which loads `AGENTS.md` automatically) or another agent that reads
it, and ask:

> Deploy this dashboard for my AWS account by following AGENTS.md.

The agent checks your tools, asks for the inputs it can't look up, and stops for your
approval before it changes anything in AWS. You do the one console-only step: turning on
the reports in the Kiro console.

## Deploy by hand

**You need:** an AWS account where users are subscribed to Kiro, permission to create the
resources (administrator, or `policies/deployer-bootstrap-policy.template.json`), the AWS
CLI v2, Terraform 1.x and Python 3.10 or newer.

1. **Reports.** If Kiro doesn't deliver user activity reports to S3 yet, create a bucket in
   your Kiro profile Region, give it the bucket policy from
   `policies/kiro-report-bucket-policy.template.json`, and turn reports on in the Kiro
   console: **Settings** → **Kiro user activity reports** → **Edit** → **Collect granular
   metrics per user**, with an S3 location such as `s3://amzn-s3-demo-bucket/user-activity/`.
   The first report arrives after the next 02:00 UTC.
2. **Settings.** `cp terraform/terraform.tfvars.example terraform/terraform.tfvars` and
   fill it in. The comments in the file explain each value.
3. **Deploy.** `./deploy.sh` runs `terraform plan`, asks before it applies, writes
   `app/.env`, and runs the Glue crawler once reports exist.
4. **Run.** `./start.sh`, then open http://localhost:8501. The first run creates a Python
   virtual environment in `app/venv`.

[AGENTS.md](AGENTS.md) has every command for each step, and a troubleshooting table.

If the dashboard runs as a role or an IAM Identity Center permission set rather than an
IAM user, leave `local_iam_user_name` empty and attach the policy from the
`dashboard_access_policy_arn` Terraform output to that role or permission set.

## Configuration

`./deploy.sh env` writes `app/.env` from the Terraform outputs. See `app/.env.example`.

| Variable | Meaning |
|---|---|
| `AWS_REGION` | Region of the Kiro profile, the reports and the Athena/Glue resources |
| `ATHENA_DATABASE` | Glue database that holds the report table |
| `ATHENA_WORKGROUP` | Athena workgroup created by Terraform |
| `ATHENA_OUTPUT_BUCKET` | Athena results location |
| `GLUE_TABLE_NAME` | Optional. Defaults to the only table in the database |
| `IDENTITY_STORE_ID` | IAM Identity Center identity store. Empty shows user IDs and hides nobody |
| `IDENTITY_STORE_REGION` | Region of IAM Identity Center, if it differs from `AWS_REGION` |

## How the numbers are calculated

- **Months** are calendar months of the report dates, which are UTC days.
- **Credits Used** in the reports already includes overage credits. Plan credits used are
  Credits Used minus Overage Credits Used, and **Used %** is Credits Used divided by the
  tier's monthly credits, so overage takes it past 100%.
- **Removed users:** the dashboard calls `identitystore:DescribeUser` for every user in the
  reports and caches the result for an hour (**Refresh Data** clears the cache). A user who
  no longer exists, or whose status is `DISABLED`, is hidden from the monthly view. A
  failed lookup hides nobody, and if every user looks removed the dashboard warns about a
  wrong `IDENTITY_STORE_ID` instead of hiding everyone.

## Limitations

- **Cancelled subscriptions** of people who are still in IAM Identity Center aren't
  detected, because Kiro has no public API that lists subscriptions. Those users stay in
  the monthly view.
- **Credit pooling:** organizations that pool credits across seats don't have per-user
  allowances, so the allowance comparison doesn't apply to them.
- **One Kiro profile** (one account and Region) per deployment.
- **Identity Center in another account:** names and removed-user detection need access to
  the identity store, which usually means the Kiro account owns the IAM Identity Center
  instance. Without it, the dashboard shows user IDs.
- **Per-model columns:** the reports' column set changes from day to day with the models
  in use. The first eleven columns (date to total messages) never move, and they are the
  only ones the dashboard reads. The later columns, such as per-model message counts,
  don't line up across files in Athena and aren't used.
- Commercial AWS Regions only.

## Costs

Small. Athena charges for data scanned ([$5 per TB](https://aws.amazon.com/athena/pricing/)
in us-east-1) with a 10 MB minimum per query, so a page view costs a fraction of a cent.
The Glue crawler runs once a day and is billed per DPU-hour with a
[10-minute minimum](https://aws.amazon.com/glue/pricing/) per run. Query results expire
after seven days.

## Security

The dashboard has no login, so `start.sh` binds it to `127.0.0.1` and only your machine
can open it. It shows the names and usage of people in your organization; treat exports
accordingly. See [SECURITY.md](SECURITY.md) for details and for reporting a vulnerability.

## Remove

`terraform -chdir=terraform destroy` deletes everything Terraform created, including the
Athena results bucket. Your report bucket and the Kiro report setting stay as they are.

## Changes from the AWS sample

- The read-only access policy goes to an IAM user of your choice, or to any role or
  permission set, instead of to an ECS/EC2/Lambda role and instance profile.
- Monthly view, allowance table and removed-user handling (above).
- Fixes: overage was counted twice in "Top 15 Users by Total Credits" and in "Base vs
  Overage"; Athena results stopped at 1,000 rows; `deploy.sh` didn't set the Athena
  workgroup; HTML exports loaded an old plotly.js that can't draw the charts.
- Queries and user lookups run in parallel, so pages load in seconds.
- Tighter permissions: the crawler role can only read the reports, and the dashboard
  policy no longer includes `identitystore:ListUsers`.

## License

[MIT No Attribution](LICENSE), the same license as the AWS sample.
