# Security

## Reporting a vulnerability

Please don't report security problems in public issues. Use GitHub's private
reporting instead: open the repository's **Security** tab and choose
**Report a vulnerability**. I'll reply as soon as I can.

## Running the dashboard safely

- **No login.** The dashboard has no authentication. `start.sh` binds it to
  `127.0.0.1`, so only the machine it runs on can open it. Don't publish it on a
  network or the internet without putting authentication in front of it.
- **Personal data.** The pages and the exports show the names and Kiro usage of
  people in your organization, and the raw reports in S3 contain their email
  addresses. Store and share exports according to your organization's data policy.
- **Least privilege.** Run the dashboard with the read-only policy that Terraform
  creates (`dashboard_access_policy_arn`). It can only query the dashboard's
  Athena workgroup, read the report prefix, write query results, and call
  `identitystore:DescribeUser`.
- **Local files stay local.** `app/.env`, `terraform/terraform.tfvars` and the
  Terraform state describe your account. `.gitignore` keeps them out of Git;
  don't commit them to a fork.
