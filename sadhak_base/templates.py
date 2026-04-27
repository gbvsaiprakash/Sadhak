def task_reminder_template_html(username, task_title, task_type, scheduled_at, scheduled_end_at, app_name="Sadhak"):
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0" />
      <title>Task Reminder</title>
    </head>
    <body style="margin:0;padding:0;background:#f5f7fb;font-family:Arial,sans-serif;color:#1f2937;">
      <table width="100%" cellpadding="0" cellspacing="0" style="padding:24px 0;">
        <tr>
          <td align="center">
            <table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:10px;padding:24px;">
              <tr>
                <td style="font-size:22px;font-weight:700;color:#111827;">Task Reminder</td>
              </tr>
              <tr><td style="height:12px;"></td></tr>
              <tr>
                <td style="font-size:15px;line-height:1.6;">
                  Hi {username},<br/><br/>
                  This is a reminder for your {task_type}:<br/>
                  <strong>{task_title}</strong><br/><br/>
                  Scheduled time: <strong>{scheduled_at}</strong> - <strong>{scheduled_end_at}</strong><br/><br/>
                  Stay consistent. You are doing great.
                </td>
              </tr>
              <tr><td style="height:20px;"></td></tr>
              <tr>
                <td style="font-size:12px;color:#6b7280;">
                  Sent by {app_name}. Please do not reply to this email.
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
    </html>
    """
