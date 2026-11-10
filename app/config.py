import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    load_dotenv(env_path)

# AWS Configuration — all values come from .env (written by `./deploy.sh env`)
AWS_REGION = os.getenv('AWS_REGION', 'us-east-1')
ATHENA_DATABASE = os.getenv('ATHENA_DATABASE', '')
ATHENA_OUTPUT_BUCKET = os.getenv('ATHENA_OUTPUT_BUCKET', '')
ATHENA_WORKGROUP = os.getenv('ATHENA_WORKGROUP', 'primary')
GLUE_TABLE_NAME = os.getenv('GLUE_TABLE_NAME', '')
IDENTITY_STORE_ID = os.getenv('IDENTITY_STORE_ID', '')
# IAM Identity Center can live in a different Region than the Kiro profile.
IDENTITY_STORE_REGION = os.getenv('IDENTITY_STORE_REGION', '') or AWS_REGION

# Streamlit Configuration
PAGE_TITLE = "Kiro Users Report"
PAGE_ICON = "⚡"
LAYOUT = "wide"
