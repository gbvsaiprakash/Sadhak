from dotenv import load_dotenv
import os

# Load variables from .env file
load_dotenv()

# Access the variables
database_url = os.getenv("DATABASE_URL")
access_token_seconds = int(os.getenv('ACCESS_TOKEN_TTL_SECONDS'))
refresh_token_seconds = int(os.getenv("REFRESH_TOKEN_TTL_SECONDS"))
access_token_cookie = os.getenv("ACCESS_TOKEN_COOKIE")
refresh_token_cookie = os.getenv("REFRESH_TOKEN_COOKIE")
account_verification_code = os.getenv("ACCOUNT_VERIFICATION_CODE")
forgot_verification_code = os.getenv("FR_PASSWORD_CODE")
account_deletion_code = os.getenv("ACCOUNT_DELETION_CODE")
ar_expiry = os.getenv("AR_EXPIRY")
fp_expiry = os.getenv("FRP_EXPIRY")
ad_expiry = os.getenv("AD_EXPIRY")

