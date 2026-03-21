import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
# from email.mime.base import MIMEBase
# from email import encoders
from email.mime.application import MIMEApplication
from email.utils import make_msgid
from datetime import datetime
from smtplib import SMTPException


def account_registration_template_html(username, otp):
    """
    HTML template for account registration email.
    """
    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Account Registration</title>
</head>
<body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f6f8fb;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:10px;overflow:hidden;">
          <tr>
            <td style="background:#111827;color:#ffffff;padding:20px 24px;font-size:20px;font-weight:700;">
              Welcome to Sadhak
            </td>
          </tr>
          <tr>
            <td style="padding:24px;">
              <p style="margin:0 0 12px 0;font-size:16px;">Hi <strong>{username}</strong>,</p>
              <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;">
                Thanks for registration. Please use the OTP below to verify your account:
              </p>
              <p style="margin:0 0 20px 0;font-size:28px;font-weight:700;letter-spacing:4px;color:#111827;">{otp}</p>
              <p style="margin:0;font-size:13px;color:#6b7280;line-height:1.6;">
                If you did not request this, you can ignore this email.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def password_reset_template_html(username, otp):
    """
    HTML template for password reset email.
    """
    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Password Reset</title>
</head>
<body style="margin:0;padding:0;background:#f6f8fb;font-family:Arial,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f6f8fb;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background:#ffffff;border-radius:10px;overflow:hidden;">
          <tr>
            <td style="background:#111827;color:#ffffff;padding:20px 24px;font-size:20px;font-weight:700;">
              Password Reset Request
            </td>
          </tr>
          <tr>
            <td style="padding:24px;">
              <p style="margin:0 0 12px 0;font-size:16px;">Hi <strong>{username}</strong>,</p>
              <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;">
                We received a request to reset your password. Use the OTP below:
              </p>
              <p style="margin:0 0 20px 0;font-size:28px;font-weight:700;letter-spacing:4px;color:#111827;">{otp}</p>
              <p style="margin:0;font-size:13px;color:#6b7280;line-height:1.6;">
                If you did not request a password reset, please ignore this email.
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""

def account_deletion_template_html(username, otp):
    """
    HTML template for account deletion request.
    """
    return f"""
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Account Deletion Request</title>
</head>
<body style="margin:0;padding:0;background:#fef2f2;font-family:Arial,sans-serif;color:#1f2937;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#fef2f2;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="background:#ffffff;border: 1px solid #fee2e2; border-radius:10px;overflow:hidden;">
          <!-- Header with Red Alert Color -->
          <tr>
            <td style="background:#dc2626;color:#ffffff;padding:20px 24px;font-size:20px;font-weight:700;">
              Confirm Account Deletion
            </td>
          </tr>
          <tr>
            <td style="padding:24px;">
              <p style="margin:0 0 12px 0;font-size:16px;">Hi <strong>{username}</strong>,</p>
              <p style="margin:0 0 16px 0;font-size:15px;line-height:1.6;">
                We received a request to <strong>permanently delete</strong> your account. This action cannot be undone. Please use the verification code below to proceed:
              </p>
              
              <!-- OTP Box -->
              <div style="background:#f9fafb; padding:20px; text-align:center; border-radius:8px; margin-bottom:20px;">
                <p style="margin:0;font-size:32px;font-weight:700;letter-spacing:6px;color:#dc2626;">{otp}</p>
              </div>

              <!-- Security Warning Box -->
              <div style="border-left: 4px solid #dc2626; background: #fff1f0; padding: 16px; margin-bottom: 20px;">
                <p style="margin:0; font-size:14px; font-weight:700; color:#991b1b;">⚠️ SECURITY ALERT</p>
                <p style="margin:8px 0 0 0; font-size:14px; line-height:1.5; color:#b91c1c;">
                  If you did <strong>NOT</strong> request this deletion, someone may have unauthorized access to your account. Please <strong>change your password immediately</strong> and contact our support team to secure your data.
                </p>
              </div>

              <p style="margin:0;font-size:13px;color:#6b7280;line-height:1.6;">
                If this was you, you can safely ignore the warning above. This code will expire shortly.
              </p>
            </td>
          </tr>
          <!-- Footer -->
          <tr>
            <td style="background:#f9fafb; padding:16px 24px; text-align:center; font-size:12px; color:#9ca3af;">
                &copy; {username.split('@')[0]} Security Team
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""


def send_email(subject, body, recipient_email, body_type='plain', attachment=None, attachment_name="attachment.pdf",is_unique_subject=True, email_config={}):
    """
    Sends an email using Google's SMTP with app-specific password.
    :param subject: Subject of the email.
    :param body: Body of the email. Can be plain text or HTML.
    :param recipient_email: Recipient email address.
    :param body_type: 'plain' for plain text emails or 'html' for HTML emails.
    :param attachment: Path to the file to be attached to the email.
    """
    

    if not email_config:
        CUSTOM_EMAIL_HOST_USER = os.getenv("HOST_EMAIL_USER")
        CUSTOM_EMAIL_HOST_PASSWORD = os.getenv("HOST_EMAIL_PASSWORD")
        CUSTOM_EMAIL_HOST = os.getenv("HOST_EMAIL_HOST")
        CUSTOM_EMAIL_PORT = os.getenv("HOST_EMAIL_PORT")
        print("custom",CUSTOM_EMAIL_HOST, CUSTOM_EMAIL_HOST_USER)
    else:
        CUSTOM_EMAIL_HOST_USER = email_config.get("default_email")
        CUSTOM_EMAIL_HOST_PASSWORD = email_config.get("default_email_password")
        CUSTOM_EMAIL_HOST = email_config.get("email_host")
        CUSTOM_EMAIL_PORT = email_config.get("email_port")

    if is_unique_subject == True:
        unique_subject = f"{subject} - {datetime.now().strftime('%Y%m%d%H%M%S')}"
    else:
        unique_subject = f"{subject}" 


    msg = MIMEMultipart("alternative")
    msg['From'] = CUSTOM_EMAIL_HOST_USER
    msg['To'] = recipient_email
    msg['Subject'] = unique_subject

    msg['Message-ID'] = make_msgid()
    
    if 'In-Reply-To' in msg:
        del msg['In-Reply-To']
    if 'References' in msg:
        del msg['References']
    
    # Add body to email, the body_type can be 'plain' or 'html'
    msg.attach(MIMEText(body, body_type))

    if attachment:
        try:
            # Add the in-memory attachment (e.g., PDF)
            mime_base = MIMEApplication(attachment, _subtype='pdf')
            mime_base.add_header('Content-Disposition', f'attachment; filename="{attachment_name}"')
            msg.attach(mime_base)
        except Exception as e:
            print(f"Failed to attach file: {e}")

    try:
        with smtplib.SMTP(CUSTOM_EMAIL_HOST, CUSTOM_EMAIL_PORT) as server:
            server.starttls()
            server.login(CUSTOM_EMAIL_HOST_USER, CUSTOM_EMAIL_HOST_PASSWORD)
            text = msg.as_string()
            failed = server.sendmail(CUSTOM_EMAIL_HOST_USER, recipient_email, text)
            if failed:
                return False,"wrong_email"
        # logger.info(msg=f"Email successfully sent to {recipient_email}", 
        #             extra={"data": {"type":"email_communication","message_data":text}})
        print(f"Email successfully sent to {recipient_email}")
        return True,"sent"
    except Exception as e:
        # error_logger.error(msg=f"Failed to send email to {recipient_email}", extra={"data": str(e)})
        print(f"Failed to send email to {recipient_email} due to {str(e)}")
        return False,"error me"
