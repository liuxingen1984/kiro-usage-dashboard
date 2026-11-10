#!/usr/bin/env bash
# Deploy the dashboard's AWS resources with Terraform and configure the app.
#
#   ./deploy.sh          plan, ask for confirmation, apply, write app/.env, run the crawler
#   ./deploy.sh env      (re)write app/.env from the Terraform outputs
#   ./deploy.sh crawl    run the Glue crawler once and wait for it to finish
set -euo pipefail
cd "$(dirname "$0")"

tf() { terraform -chdir=terraform "$@"; }
tf_out() { tf output -raw "$1"; }

need() {
    command -v "$1" >/dev/null 2>&1 || { echo "❌ $1 is required. Install it from $2"; exit 1; }
}

write_env() {
    cat > app/.env << EOF
# Written by deploy.sh on $(date -u '+%Y-%m-%d %H:%M UTC') from the Terraform outputs. Do not commit.
AWS_REGION=$(tf_out aws_region)
ATHENA_DATABASE=$(tf_out glue_database_name)
ATHENA_WORKGROUP=$(tf_out athena_workgroup_name)
ATHENA_OUTPUT_BUCKET=$(tf_out athena_output_location)
IDENTITY_STORE_ID=$(tf_out identity_store_id)
IDENTITY_STORE_REGION=$(tf_out identity_store_region)
EOF
    echo "✅ Wrote app/.env"
    cat app/.env
}

run_crawler() {
    local name region path csv_count status
    name=$(tf_out glue_crawler_name)
    region=$(tf_out aws_region)
    path=$(tf_out s3_data_path)

    csv_count=$( (aws s3 ls "$path" --recursive --region "$region" || true) | grep -c '\.csv$' || true)
    if [ "$csv_count" = "0" ]; then
        echo "⚠️  No report CSV files under $path yet."
        echo "   Kiro writes the first report at the next 02:00 UTC after you enable it."
        echo "   Run './deploy.sh crawl' once the first files have arrived."
        return 0
    fi

    crawler_state() { aws glue get-crawler --name "$name" --region "$region" --query 'Crawler.State' --output text; }

    echo "🕷  Starting Glue crawler $name ($csv_count report files)"
    aws glue start-crawler --name "$name" --region "$region" >/dev/null || echo "   The crawler is probably running already."
    # Wait for the run to start, then for it to finish.
    for _ in $(seq 1 15); do
        [ "$(crawler_state)" != "READY" ] && break
        sleep 2
    done
    while [ "$(crawler_state)" != "READY" ]; do
        echo "   crawler state: $(crawler_state)"
        sleep 10
    done
    status=$(aws glue get-crawler --name "$name" --region "$region" --query 'Crawler.LastCrawl.Status' --output text)
    if [ "$status" != "SUCCEEDED" ]; then
        echo "❌ Crawler finished with status $status. See the /aws-glue/crawlers log group in CloudWatch."
        exit 1
    fi
    echo "✅ Crawler finished: $status"
}

deploy() {
    need terraform "https://developer.hashicorp.com/terraform/install"
    need aws "https://aws.amazon.com/cli/"

    echo "📊 Kiro Usage Dashboard — deploy"
    echo "AWS identity: $(aws sts get-caller-identity --query Arn --output text)"

    if [ ! -f terraform/terraform.tfvars ]; then
        cp terraform/terraform.tfvars.example terraform/terraform.tfvars
        echo "⚠️  Created terraform/terraform.tfvars from the example."
        echo "   Fill in your values, then run ./deploy.sh again."
        exit 1
    fi

    tf init -input=false
    tf plan -input=false -out=tfplan
    read -r -p "Apply this plan? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        rm -f terraform/tfplan
        echo "Cancelled."
        exit 0
    fi
    tf apply -input=false tfplan
    rm -f terraform/tfplan

    write_env
    run_crawler
    echo ""
    echo "✅ Deploy complete. Start the dashboard with ./start.sh"
}

case "${1:-deploy}" in
    deploy) deploy ;;
    env) write_env ;;
    crawl) run_crawler ;;
    *) echo "Usage: $0 [deploy|env|crawl]"; exit 2 ;;
esac
