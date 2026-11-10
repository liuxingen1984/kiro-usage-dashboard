# AGENTS.md

Instructions for AI agents (Kiro, Claude Code, Codex, Cursor and others) that help a
Kiro administrator deploy this dashboard. People can read [README.md](README.md) instead.

## What you are deploying

1. Kiro writes a daily CSV report of per-user activity to an S3 bucket in the
   administrator's AWS account.
2. Terraform in `terraform/` creates a Glue crawler and database for those reports, an
   Athena workgroup with a results bucket, and a read-only IAM policy for the dashboard
   (12 resources, 13 with the optional IAM user attachment).
3. The Streamlit app in `app/` queries Athena and runs on the administrator's machine at
   http://localhost:8501.

## Rules

- **Ask before you change anything in AWS.** That includes creating a bucket, changing a
  bucket policy, `terraform apply`, `terraform destroy`, and attaching IAM policies. Say
  what will change, then wait for an explicit yes. Read-only commands such as
  `aws sts get-caller-identity`, `aws s3 ls`, `aws glue get-*` and `terraform plan` need
  no approval.
- **Never commit, paste or upload private files or data.** That covers `app/.env`,
  `terraform/terraform.tfvars`, `terraform/*.tfstate*`, AWS credentials, and the report
  CSVs, which contain users' email addresses. `.gitignore` already excludes these files;
  don't weaken it.
- **Use the AWS credentials the person already has.** Ask which `AWS_PROFILE` to use. Don't
  create IAM users or access keys.
- **Hand console-only steps to the person.** The Kiro console has no API for its settings.
  Tell the person exactly what to click and wait for them to confirm.
- **Deploy; don't redesign.** Don't edit the application code to get a deployment working.
  If a step fails, check the troubleshooting table below, then report what you found.
- **Expect a wait.** Kiro writes the first report at the next 02:00 UTC after reports are
  turned on. Tell the person early.

## Step 1: Check the tools and the account

```bash
aws --version              # AWS CLI v2
terraform version          # 1.x
python3 --version          # 3.10 or newer
aws sts get-caller-identity
```

Confirm with the person that the account shown is the one where users are subscribed to
Kiro. The identity needs administrator access, or at least the permissions in
`policies/deployer-bootstrap-policy.template.json`. This project supports the commercial
AWS partition only (not GovCloud).

## Step 2: Collect the inputs

Copy `terraform/terraform.tfvars.example` to `terraform/terraform.tfvars` and fill it in.

| tfvars key | What it is | How to find it |
|---|---|---|
| `aws_region` | Region of the Kiro profile: `us-east-1` or `eu-central-1` | The Region in which the Kiro console lists the subscribed users |
| `aws_account_id` | Account where users are subscribed | `aws sts get-caller-identity --query Account --output text` |
| `s3_bucket_name` | Report bucket and prefix without `s3://`, e.g. `amzn-s3-demo-bucket/user-activity` | If reports are already on, the S3 location is in Kiro console → **Settings** → **Kiro user activity reports**. Otherwise agree on a new name with the person (Step 3). |
| `identity_store_id` | IAM Identity Center identity store, e.g. `d-1234567890` | `aws sso-admin list-instances --region <identity-center-region> --query 'Instances[].IdentityStoreId' --output text`, or IAM Identity Center console → **Settings**. Leave empty if Kiro users are not in IAM Identity Center. |
| `identity_store_region` | Region of the IAM Identity Center instance | Same place as above. Leave empty if it equals `aws_region`. |
| `local_iam_user_name` | Existing IAM user that will run the dashboard | Leave empty when the dashboard runs with a role or an IAM Identity Center permission set (Step 4 attaches the policy instead). |

User names and the hiding of removed users need `identitystore:DescribeUser` on the
identity store. That works when the account in `aws_account_id` owns the IAM Identity
Center instance (a standalone account, the management account or a delegated
administrator). Otherwise the dashboard still works but shows user IDs and hides nobody.

## Step 3: Report bucket and Kiro export

Skip this step if reports already arrive:

```bash
aws s3 ls "s3://<bucket>/<prefix>/AWSLogs/<account-id>/KiroLogs/user_report/<region>/" --recursive | tail -3
```

Otherwise:

1. **[Ask first] Create the bucket** in the Kiro profile Region and account. New buckets
   block public access and are encrypted by default.

   ```bash
   aws s3api create-bucket --bucket <bucket> --region us-east-1
   # any other Region:
   aws s3api create-bucket --bucket <bucket> --region <region> \
     --create-bucket-configuration LocationConstraint=<region>
   ```

2. **[Ask first] Let Kiro write to the prefix.** Kiro can't write to the bucket root, so a
   prefix is required. Render the policy template:

   ```bash
   sed -e 's/<REPORT_BUCKET>/<bucket>/g' -e 's#<REPORT_PREFIX>#<prefix>#g' \
       -e 's/<ACCOUNT_ID>/<account-id>/g' -e 's/<REGION>/<region>/g' \
       policies/kiro-report-bucket-policy.template.json > /tmp/kiro-report-bucket-policy.json
   aws s3api get-bucket-policy --bucket <bucket>   # an error here means there is no policy yet
   aws s3api put-bucket-policy --bucket <bucket> --policy file:///tmp/kiro-report-bucket-policy.json
   ```

   If the bucket already has a policy, add the `KiroLogsWrite` statement to it instead of
   replacing it.

3. **Ask the person to turn on the reports.** In the Kiro console (in the Kiro profile
   Region): **Settings** → **Kiro user activity reports** → **Edit** → turn on
   **Collect granular metrics per user** → set **S3 location** to `s3://<bucket>/<prefix>/`
   → **Save**. This needs Kiro administrator permissions.

Continue with Step 4 without waiting for the first report.

## Step 4: Deploy the AWS resources

```bash
terraform -chdir=terraform init -input=false
terraform -chdir=terraform plan -input=false -out=tfplan
```

Summarize the plan for the person. A first deployment adds 12 resources (13 with
`local_iam_user_name`) and changes or destroys nothing. **[Ask first]** then apply it and
configure the app:

```bash
terraform -chdir=terraform apply -input=false tfplan && rm -f terraform/tfplan
./deploy.sh env     # writes app/.env from the Terraform outputs
./deploy.sh crawl   # builds the table; prints a notice and exits if no report has arrived yet
```

If `local_iam_user_name` is empty, **[ask first]** attach the policy from
`terraform -chdir=terraform output -raw dashboard_access_policy_arn` to the role the
dashboard will run as. For an IAM Identity Center permission set, add the policy as a
customer managed policy by its name (`<project_name>-athena-access`, by default
`kiro-user-report-dashboard-athena-access`).

People can run `./deploy.sh` instead: it does all of Step 4 and asks before applying.

## Step 5: Start and check the dashboard

`start.sh` creates `app/venv` on its first run and then keeps running, so start it in the
background or a separate terminal. Use the person's profile if the dashboard runs as a
different identity than the deployment, e.g. `AWS_PROFILE=<profile> ./start.sh`.

```bash
./start.sh --server.headless true
curl -s http://127.0.0.1:8501/_stcore/health   # prints "ok"
```

Ask the person to open http://localhost:8501. The Monthly view for the current month
opens first. Until the first report has been crawled, the page shows "No tables found in
Glue database"; after the next 02:00 UTC, run `./deploy.sh crawl` and press
**Refresh Data**. The crawler also runs daily at 03:00 UTC.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `No tables found in Glue database` | No report yet, or the crawler hasn't run | Wait for the first report, then `./deploy.sh crawl` |
| `AccessDenied` on the workgroup or `StartQueryExecution` | `ATHENA_WORKGROUP` missing or wrong, or the policy isn't attached to the identity running the app | `./deploy.sh env`; check the attachment from Step 4 |
| `./deploy.sh crawl` finds no CSV files after 02:00 UTC | The Kiro S3 location doesn't match `s3_bucket_name`, the bucket policy is missing, or the bucket is in another Region | Compare the Kiro setting with `terraform -chdir=terraform output s3_data_path`; recheck Step 3 |
| Users appear as long IDs | `IDENTITY_STORE_ID` empty or wrong, wrong `IDENTITY_STORE_REGION`, or no access to the identity store | Fix `identity_store_id` / `identity_store_region`, then `terraform apply` and `./deploy.sh env` |
| Warning that every user looks removed | `IDENTITY_STORE_ID` points to a different identity store | Same as above |
| `start.sh` says Python 3.10+ is required | An old `python3` is first on the PATH (macOS ships 3.9) | `PYTHON=/path/to/python3.12 ./start.sh` |

## Updating and removing

- **Update:** `git pull`, run `./deploy.sh` (it shows the plan and asks first), and
  restart `./start.sh`. After a change to `app/requirements.txt`, delete `app/venv` first.
- **[Ask first] Remove:** `terraform -chdir=terraform destroy`. This deletes the Glue,
  Athena and IAM resources and the Athena results bucket. It doesn't touch the report
  bucket or the Kiro report setting; turn reports off in the Kiro console if they're no
  longer needed.

## Files

| Path | Purpose |
|---|---|
| `app/app.py` | The dashboard |
| `app/config.py`, `app/.env.example` | Settings, read from `app/.env` |
| `deploy.sh` | Terraform plan/apply, `env` and `crawl` helpers |
| `start.sh` | Creates the virtual environment and starts Streamlit on 127.0.0.1 |
| `terraform/` | AWS resources |
| `policies/` | Templates: the bucket policy Kiro needs, and least-privilege permissions for the deployer |
| `tools/generate_sample_data.py` | Fake reports for a test deployment; never upload them to the real report prefix |
