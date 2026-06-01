"""
Management command to test creating a recurring task and syncing to Google Calendar.
Usage: python manage.py create_recurring_task --username john_doe --title "Daily Standup" --frequency daily --start-date 2024-01-01 --end-date 2024-12-31
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime, date
from tracker.models import Task
from user_management.models import User
from integrations.rrule_handler import RRuleHandler


class Command(BaseCommand):
    help = "Create a recurring task and sync to Google Calendar"

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            type=str,
            required=True,
            help="Username of the user",
        )
        parser.add_argument(
            "--title",
            type=str,
            required=True,
            help="Task title",
        )
        parser.add_argument(
            "--description",
            type=str,
            default="",
            help="Task description",
        )
        parser.add_argument(
            "--frequency",
            type=str,
            choices=["daily", "weekly", "monthly", "yearly"],
            required=True,
            help="Frequency type",
        )
        parser.add_argument(
            "--start-date",
            type=str,
            required=True,
            help="Start date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--end-date",
            type=str,
            help="End date (YYYY-MM-DD)",
        )
        parser.add_argument(
            "--start-time",
            type=str,
            default="09:00:00",
            help="Start time (HH:MM:SS, default 09:00:00)",
        )
        parser.add_argument(
            "--end-time",
            type=str,
            default="10:00:00",
            help="End time (HH:MM:SS, default 10:00:00)",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=1,
            help="Frequency interval (default 1)",
        )
        parser.add_argument(
            "--weekdays",
            type=str,
            help="Comma-separated weekdays for weekly (0=Mon, 6=Sun, e.g. 0,2,4)",
        )
        parser.add_argument(
            "--day-of-month",
            type=int,
            help="Day of month for monthly (1-31)",
        )

    def handle(self, *args, **options):
        username = options["username"]
        title = options["title"]
        description = options.get("description", "")
        frequency = options["frequency"]
        start_date_str = options["start_date"]
        end_date_str = options.get("end_date")
        start_time_str = options.get("start_time", "09:00:00")
        end_time_str = options.get("end_time", "10:00:00")
        interval = options.get("interval", 1)
        weekdays = options.get("weekdays")
        day_of_month = options.get("day_of_month")

        # Get user
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"User {username} not found"))
            return

        # Parse dates
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date = None
            if end_date_str:
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            start_time = datetime.strptime(start_time_str, "%H:%M:%S").time()
            end_time = datetime.strptime(end_time_str, "%H:%M:%S").time()
        except ValueError as e:
            self.stdout.write(self.style.ERROR(f"Invalid date/time format: {e}"))
            return

        # Build RRULE
        try:
            rrule_str = RRuleHandler.build_rrule(
                frequency_type=frequency,
                start_date=datetime.combine(start_date, start_time),
                frequency_interval=interval,
                end_date=datetime.combine(end_date, start_time) if end_date else None,
                frequency_days=weekdays,
                day_of_month=day_of_month,
            )
        except ValueError as e:
            self.stdout.write(self.style.ERROR(f"Error building RRULE: {e}"))
            return

        # Create task
        try:
            task = Task.objects.create(
                user=user,
                title=title,
                description=description,
                section="personal",
                status="pending",
                frequency_type="once",  # Set to once since we're using RRULE
                start_date=start_date,
                end_date=end_date,
                start_time=start_time,
                end_time=end_time,
                recurrence_rule=rrule_str,
                is_deleted=False,
            )

            self.stdout.write(
                self.style.SUCCESS(f"✓ Created recurring task {task.id}")
            )
            self.stdout.write(f"  Title: {title}")
            self.stdout.write(f"  Frequency: {frequency}")
            self.stdout.write(f"  RRULE: {rrule_str}")
            self.stdout.write(f"  Start: {start_date} {start_time}")
            if end_date:
                self.stdout.write(f"  End: {end_date}")

            # Check if synced to Google
            if task.google_event_id:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Synced to Google Calendar: {task.google_event_id}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "⚠ Not synced to Google Calendar (calendar may not be connected)"
                    )
                )

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error creating task: {e}"))
            raise
