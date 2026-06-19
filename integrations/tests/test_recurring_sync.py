"""
Tests for recurring task syncing to Google Calendar.
Run with: python manage.py test integrations.tests.test_recurring_sync
"""

import logging
import uuid
from datetime import datetime, date, timedelta, timezone as dt_timezone
import urllib.error
import urllib.parse
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from unittest.mock import patch, MagicMock
from integrations.services import (
    create_recurring_google_event,
    google_list_events,
    pull_google_delta_for_watch,
    push_local_occurrence_change,
    sync_parent_occurrences_to_google,
    sync_google_recurring_to_app,
    sync_google_recurring_change,
    delete_task_occurrence_in_app,
    delete_parent_from_google,
    _update_rrule_until_date,
    sync_external_google_event_to_app,
    sync_parent_reminders_to_google,
    sync_google_status_to_app_occurrences,
    _google_request_json_with_retry,
)
from integrations.models import EventSyncMap, GoogleCalendarConnection, GoogleCalendarWatch
from tracker.models import Task, TaskOccurrence, Habit
from tracker.services.occurrence import ensure_future_occurrences
from integrations.rrule_handler import RRuleHandler

logger = logging.getLogger(__name__)
User = get_user_model()


class RecurringTaskSyncTests(TestCase):
    """Test syncing recurring tasks to Google Calendar."""
    
    def setUp(self):
        """Set up test user and Google Calendar connection."""
        self.user = User.objects.create(
            username="testuser",
            email="test@example.com",
        )
        
        # Create a mock Google Calendar connection
        self.connection = GoogleCalendarConnection.objects.create(
            user=self.user,
            email="user@gmail.com",
            google_sub="google_123",
            refresh_token="encrypted_refresh_token",
            access_token="encrypted_access_token",
            token_expiry=timezone.now(),
            scope="calendar",
            is_active=True,
        )
    
    @patch('integrations.services._google_request_json_with_retry')
    @patch('integrations.services._google_request_json')
    @patch('integrations.services.ensure_valid_access_token')
    def test_create_recurring_google_event_daily(self, mock_token, mock_request, mock_retry_request):
        """Test creating a daily recurring event in Google Calendar."""
        mock_token.return_value = "access_token"
        mock_request.return_value = {
            "id": "google_event_123",
            "etag": "etag_123",
        }
        mock_retry_request.return_value = {
            "id": "google_event_123",
            "etag": "etag_123",
        }
        
        # Create task with daily recurrence
        task = Task.objects.create(
            user=self.user,
            title="Daily Standup",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            start_time=datetime.strptime("09:00:00", "%H:%M:%S").time(),
            end_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20241231",
        )
        
        # Create recurring event
        result = create_recurring_google_event(self.user, task)
        
        self.assertTrue(result["created"])
        self.assertEqual(result["google_event_id"], "google_event_123")
        
        # Verify task was updated with google_event_id
        task.refresh_from_db()
        self.assertEqual(task.google_event_id, "google_event_123")
        
        # Verify EventSyncMap was created
        mapping = EventSyncMap.objects.get(local_task_id=task.id)
        self.assertTrue(mapping.is_recurring)
        self.assertEqual(mapping.recurrence_rule, task.recurrence_rule)
        logger.info(f"Created recurring event: {result}")

    @patch('integrations.services._google_request_json_with_retry')
    @patch('integrations.services._google_request_json')
    @patch('integrations.services.ensure_valid_access_token')
    def test_create_recurring_google_event_includes_reminders(self, mock_token, mock_request, mock_retry_request):
        mock_token.return_value = "access_token"
        mock_request.return_value = {"id": "google_event_with_reminders", "etag": "etag_reminders"}
        mock_retry_request.return_value = {"id": "google_event_with_reminders", "etag": "etag_reminders"}

        task = Task.objects.create(
            user=self.user,
            title="Reminder Task",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 5),
            start_time=datetime.strptime("09:00:00", "%H:%M:%S").time(),
            end_time=datetime.strptime("09:30:00", "%H:%M:%S").time(),
            reminder_enabled=True,
            reminder_offset=[{"value": 15, "unit": "minutes", "modes": ["push"]}],
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20260605",
        )

        result = create_recurring_google_event(self.user, task)

        self.assertTrue(result["created"])
        payload = mock_request.call_args[0][3]
        self.assertEqual(payload["reminders"]["useDefault"], False)
        self.assertEqual(payload["reminders"]["overrides"][0]["method"], "popup")
        self.assertEqual(payload["reminders"]["overrides"][0]["minutes"], 15)

    @patch('integrations.services._google_request_json_with_retry')
    @patch('integrations.services.ensure_valid_access_token')
    def test_sync_parent_reminders_to_google_pushes_google_reminders(self, mock_token, mock_request):
        mock_token.return_value = "access_token"
        mock_request.return_value = {"etag": "etag_push_reminders"}

        task = Task.objects.create(
            user=self.user,
            title="Reminder Push",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            reminder_enabled=True,
            reminder_offset=[{"value": 1, "unit": "hours", "modes": ["push", "email"]}],
            google_event_id="google_push_event_1",
        )

        result = sync_parent_reminders_to_google(self.user, task)

        self.assertTrue(result["synced"])
        payload = mock_request.call_args[0][3]
        self.assertFalse(payload["reminders"]["useDefault"])
        self.assertEqual(len(payload["reminders"]["overrides"]), 2)

    @patch('integrations.services._google_request_json_with_retry')
    def test_google_list_events_uses_verified_at_for_initial_sync(self, mock_request):
        self.user.verified_at = timezone.make_aware(datetime(2026, 1, 2, 3, 4, 5))
        self.user.save(update_fields=["verified_at", "updated_at"])
        mock_request.return_value = {"items": []}

        google_list_events("access_token", user=self.user)

        url = mock_request.call_args[0][1]
        expected = urllib.parse.quote(self.user.verified_at.astimezone(dt_timezone.utc).isoformat())
        self.assertIn(f"timeMin={expected}", url)

    @patch('integrations.services.google_list_events')
    @patch('integrations.services.ensure_valid_access_token')
    def test_pull_google_delta_for_watch_passes_user(self, mock_token, mock_list_events):
        mock_token.return_value = ("access_token", "")
        mock_list_events.return_value = {"items": [], "nextSyncToken": "sync_123"}
        watch = GoogleCalendarWatch.objects.create(
            user=self.user,
            calendar_id="primary",
            channel_id="channel_123",
            resource_id="resource_123",
            sync_token=None,
            is_active=True,
        )

        result = pull_google_delta_for_watch(watch)

        self.assertEqual(result["next_sync_token"], "sync_123")
        self.assertEqual(mock_list_events.call_args.kwargs["user"], self.user)

    @patch('integrations.services._google_request_json_with_retry')
    @patch('integrations.services.ensure_valid_access_token')
    def test_sync_parent_reminders_to_google_uses_etag(self, mock_token, mock_request):
        mock_token.return_value = "access_token"
        mock_request.return_value = {"etag": "etag_after_patch"}

        task = Task.objects.create(
            user=self.user,
            title="Reminder ETag",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            reminder_enabled=True,
            reminder_offset=[{"value": 1, "unit": "hours", "modes": ["push", "email"]}],
            google_event_id="google_push_event_2",
        )
        EventSyncMap.objects.create(
            user=self.user,
            local_occurrence_id=task.id,
            local_parent_type="task",
            local_task_id=task.id,
            google_event_id=task.google_event_id,
            calendar_id="primary",
            google_etag="etag_before_patch",
        )

        result = sync_parent_reminders_to_google(self.user, task)

        self.assertTrue(result["synced"])
        self.assertEqual(mock_request.call_args.kwargs["extra_headers"], {"If-Match": "etag_before_patch"})

    def test_duration_config_is_normalized_on_save_for_task_and_habit(self):
        task = Task.objects.create(
            user=self.user,
            title="Duration Task",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            duration_config={"value": 45, "unit": "minutes", "mode": "email"},
        )
        habit = Habit.objects.create(
            user=self.user,
            title="Duration Habit",
            section="personal",
            status="active",
            frequency_type="daily",
            frequency_interval=1,
            start_date=date(2026, 6, 8),
            start_time=datetime.strptime("11:00:00", "%H:%M:%S").time(),
            duration_config={"value": 20, "unit": "minutes", "mode": "push"},
        )

        task.refresh_from_db()
        habit.refresh_from_db()

        self.assertEqual(task.duration_config, {"value": 45, "unit": "minutes"})
        self.assertEqual(habit.duration_config, {"value": 20, "unit": "minutes"})

    def test_ensure_future_occurrences_skips_google_imported_habits(self):
        habit = Habit.objects.create(
            user=self.user,
            title="Google Habit",
            section="personal",
            status="active",
            frequency_type="daily",
            frequency_interval=1,
            start_date=date(2026, 6, 8),
            start_time=datetime.strptime("11:00:00", "%H:%M:%S").time(),
            external_google_id=True,
            google_event_id="google_habit_1",
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20260630",
        )

        result = ensure_future_occurrences(habit)

        self.assertEqual(result, [])
        self.assertEqual(TaskOccurrence.objects.filter(habit=habit).count(), 0)

    @patch('integrations.services._google_request_no_content')
    @patch('integrations.services.ensure_valid_access_token')
    def test_delete_parent_from_google_deletes_recurring_root(self, mock_token, mock_delete):
        mock_token.return_value = "access_token"

        task = Task.objects.create(
            user=self.user,
            title="Delete Root",
            section="personal",
            status="pending",
            frequency_type="daily",
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20260630",
            google_event_id="root_series_delete",
        )
        EventSyncMap.objects.create(
            user=self.user,
            local_occurrence_id=task.id,
            local_parent_type="task",
            local_task_id=task.id,
            google_event_id=task.google_event_id,
            calendar_id="primary",
            google_etag="etag_root_delete",
            is_recurring=True,
            recurrence_rule=task.recurrence_rule,
        )

        result = delete_parent_from_google(self.user, task)

        self.assertTrue(result["deleted"])
        self.assertEqual(mock_delete.call_count, 1)

    @patch('integrations.services.delete_google_event_from_calendar')
    def test_delete_parent_from_google_deletes_non_recurring_occurrences(self, mock_delete):
        task = Task.objects.create(
            user=self.user,
            title="Delete Children",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
        )
        occ1 = TaskOccurrence.objects.create(task=task, scheduled_date=date(2026, 6, 8), scheduled_time=datetime.strptime("10:00:00", "%H:%M:%S").time(), status="pending")
        occ2 = TaskOccurrence.objects.create(task=task, scheduled_date=date(2026, 6, 9), scheduled_time=datetime.strptime("10:00:00", "%H:%M:%S").time(), status="pending")
        EventSyncMap.objects.create(
            user=self.user,
            local_occurrence_id=occ1.id,
            local_parent_type="task",
            google_event_id="google_delete_1",
            calendar_id="primary",
        )
        EventSyncMap.objects.create(
            user=self.user,
            local_occurrence_id=occ2.id,
            local_parent_type="task",
            google_event_id="google_delete_2",
            calendar_id="primary",
        )
        mock_delete.return_value = {"deleted": True, "google_event_id": "google_delete_1"}

        result = delete_parent_from_google(self.user, task)

        self.assertTrue(result["deleted"])
        self.assertEqual(result["deleted_occurrences"], 2)
        self.assertEqual(mock_delete.call_count, 2)

    @patch('integrations.services.push_local_occurrence_change')
    def test_sync_parent_occurrences_to_google_pushes_one_off_task(self, mock_push):
        task = Task.objects.create(
            user=self.user,
            title="One Off",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
        )
        TaskOccurrence.objects.create(
            task=task,
            scheduled_date=date(2026, 6, 8),
            scheduled_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            status="pending",
        )
        mock_push.return_value = {"pushed": True}

        result = sync_parent_occurrences_to_google(self.user, task)

        self.assertTrue(result["synced"])
        self.assertEqual(result["pushed_occurrences"], 1)
        self.assertEqual(mock_push.call_count, 1)

    @patch('integrations.services._google_request_json_with_retry')
    @patch('integrations.services._google_request_json')
    @patch('integrations.services.ensure_valid_access_token')
    def test_push_local_occurrence_change_uses_ist_timezone(self, mock_token, mock_request, mock_retry_request):
        mock_token.return_value = "access_token"
        mock_request.return_value = {"id": "google_ist_event", "etag": "etag_ist"}
        mock_retry_request.return_value = {"id": "google_ist_event", "etag": "etag_ist"}

        task = Task.objects.create(
            user=self.user,
            title="IST Event",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
        )
        occurrence = TaskOccurrence.objects.create(
            task=task,
            scheduled_date=date(2026, 6, 8),
            scheduled_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            schedule_end_time=datetime.strptime("10:30:00", "%H:%M:%S").time(),
            status="pending",
        )

        result = push_local_occurrence_change(self.user, occurrence, action="create")

        self.assertTrue(result["pushed"])
        payload = mock_request.call_args[0][3]
        self.assertEqual(payload["start"]["timeZone"], "Asia/Kolkata")
        self.assertEqual(payload["end"]["timeZone"], "Asia/Kolkata")
        self.assertTrue(payload["start"]["dateTime"].endswith("+05:30"))
        self.assertTrue(payload["end"]["dateTime"].endswith("+05:30"))

    @patch('integrations.services.delete_google_event_from_calendar')
    @patch('integrations.services.push_local_occurrence_change')
    def test_sync_parent_occurrences_to_google_deletes_soft_deleted_occurrences(self, mock_push, mock_delete):
        task = Task.objects.create(
            user=self.user,
            title="Duration Change",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
        )
        old_occurrence = TaskOccurrence.objects.create(
            task=task,
            scheduled_date=date(2026, 6, 8),
            scheduled_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            status="pending",
            is_deleted=True,
        )
        new_occurrence = TaskOccurrence.objects.create(
            task=task,
            scheduled_date=date(2026, 6, 9),
            scheduled_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            status="pending",
        )
        EventSyncMap.objects.create(
            user=self.user,
            local_occurrence_id=old_occurrence.id,
            local_parent_type="task",
            google_event_id="google_old_duration",
            calendar_id="primary",
            google_etag="etag_old",
            is_deleted=False,
        )
        mock_delete.return_value = {"deleted": True, "google_event_id": "google_old_duration"}
        mock_push.return_value = {"pushed": True}

        result = sync_parent_occurrences_to_google(self.user, task)

        self.assertTrue(result["synced"])
        self.assertEqual(result["deleted_occurrences"], 1)
        self.assertEqual(result["pushed_occurrences"], 1)
        self.assertEqual(mock_delete.call_count, 1)
        self.assertEqual(mock_push.call_count, 1)

    @patch('integrations.services.push_local_occurrence_change')
    def test_sync_parent_occurrences_to_google_returns_failed_status_on_access_token_issue(self, mock_push):
        task = Task.objects.create(
            user=self.user,
            title="Token Issue",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 8),
            end_date=date(2026, 6, 8),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
        )
        TaskOccurrence.objects.create(
            task=task,
            scheduled_date=date(2026, 6, 8),
            scheduled_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            status="pending",
        )
        mock_push.return_value = {"pushed": False, "reason": "access_token_error"}

        result = sync_parent_occurrences_to_google(self.user, task)

        self.assertFalse(result["synced"])
        self.assertEqual(result["reason"], "access_token_error")
    
    @patch('integrations.services._google_request_json_with_retry')
    @patch('integrations.services._google_request_json')
    @patch('integrations.services.ensure_valid_access_token')
    def test_create_recurring_google_event_weekly(self, mock_token, mock_request, mock_retry_request):
        """Test creating a weekly recurring event (Mon, Wed, Fri)."""
        mock_token.return_value = "access_token"
        mock_request.return_value = {
            "id": "google_event_456",
            "etag": "etag_456",
        }
        mock_retry_request.return_value = {
            "id": "google_event_456",
            "etag": "etag_456",
        }
        
        # Create weekly recurrence (Mon, Wed, Fri)
        rrule = RRuleHandler.build_rrule(
            frequency_type="weekly",
            start_date=datetime(2024, 1, 1, 10, 0),
            frequency_days="0,2,4",
            end_date=datetime(2024, 12, 31),
        )
        
        task = Task.objects.create(
            user=self.user,
            title="Weekly Team Meeting",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            start_time=datetime.strptime("14:00:00", "%H:%M:%S").time(),
            end_time=datetime.strptime("15:00:00", "%H:%M:%S").time(),
            recurrence_rule=rrule,
        )
        
        result = create_recurring_google_event(self.user, task)
        
        self.assertTrue(result["created"])
        self.assertEqual(result["google_event_id"], "google_event_456")
        
        # Verify Google API was called with correct recurrence
        self.assertTrue(mock_request.called)
        call_args = mock_request.call_args
        payload = call_args[0][3]  # 4th argument is the payload
        
        self.assertIn("recurrence", payload)
        self.assertIsInstance(payload["recurrence"], list)
        self.assertTrue(payload["recurrence"][0].startswith("RRULE:"))
        logger.info(f"Weekly recurring event created: {payload['recurrence']}")
    
    @patch('integrations.services._google_request_json_with_retry')
    @patch('integrations.services._google_request_json')
    @patch('integrations.services.ensure_valid_access_token')
    def test_create_recurring_google_event_monthly(self, mock_token, mock_request, mock_retry_request):
        """Test creating a monthly recurring event."""
        mock_token.return_value = "access_token"
        mock_request.return_value = {
            "id": "google_event_789",
            "etag": "etag_789",
        }
        mock_retry_request.return_value = {
            "id": "google_event_789",
            "etag": "etag_789",
        }
        
        # Create monthly recurrence on the 15th
        rrule = RRuleHandler.build_rrule(
            frequency_type="monthly",
            start_date=datetime(2024, 1, 15, 10, 0),
            day_of_month=15,
            end_date=datetime(2024, 12, 31),
        )
        
        task = Task.objects.create(
            user=self.user,
            title="Monthly Review",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 15),
            end_date=date(2024, 12, 31),
            start_time=datetime.strptime("18:00:00", "%H:%M:%S").time(),
            recurrence_rule=rrule,
        )
        
        result = create_recurring_google_event(self.user, task)
        
        self.assertTrue(result["created"])
        self.assertIn("BYMONTHDAY=15", task.recurrence_rule)
        logger.info(f"Monthly recurring event created: {rrule}")
    
    def test_create_recurring_event_without_recurrence_rule(self):
        """Test that tasks without recurrence_rule are skipped."""
        task = Task.objects.create(
            user=self.user,
            title="One-off Task",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 1),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            # No recurrence_rule
        )
        
        result = create_recurring_google_event(self.user, task)
        
        self.assertFalse(result["created"])
        self.assertIn("no recurrence_rule", result["error"])
    
    def test_create_recurring_event_no_google_connection(self):
        """Test that sync fails gracefully without Google Calendar connection."""
        # Create user without Google Calendar connection
        user_no_google = User.objects.create(
            username="nogoogle",
            email="nogoogle@example.com",
        )
        
        task = Task.objects.create(
            user=user_no_google,
            title="Recurring Task",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2024, 1, 1),
            start_time=datetime.strptime("10:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY",
        )
        
        result = create_recurring_google_event(user_no_google, task)
        
        self.assertFalse(result["created"])
        self.assertIn("not connected", result["error"])
        logger.info("Gracefully handled missing Google Calendar connection")


class EventSyncMapRecurringTests(TestCase):
    """Test EventSyncMap for recurring events."""
    
    def setUp(self):
        self.user = User.objects.create(
            username="testuser",
            email="test@example.com",
        )
    
    def test_event_sync_map_recurring_fields(self):
        """Test that EventSyncMap correctly stores recurring event metadata."""
        task_id = uuid.uuid4()
        mapping = EventSyncMap.objects.create(
            user=self.user,
            local_task_id=task_id,
            local_occurrence_id=task_id,
            local_parent_type="task",
            google_event_id="google_event_123",
            calendar_id="primary",
            is_recurring=True,
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20241231",
            google_etag="etag_123",
            is_deleted=False,
        )
        
        # Verify fields are stored correctly
        self.assertTrue(mapping.is_recurring)
        self.assertEqual(mapping.recurrence_rule, "FREQ=DAILY;INTERVAL=1;UNTIL=20241231")
        self.assertEqual(mapping.google_etag, "etag_123")
        self.assertEqual(mapping.local_task_id, task_id)
        logger.info(f"EventSyncMap created for recurring task: {mapping.id}")


class GoogleRecurringPullTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="googlepull",
            email="googlepull@example.com",
        )

    def test_sync_google_recurring_to_app_creates_task_and_occurrences(self):
        result = sync_google_recurring_to_app(
            self.user,
            {
                "id": "google_series_123",
                "etag": "etag_series_123",
                "summary": "Daily Reflection",
                "description": "Imported from Google",
                "start": {"dateTime": "2026-06-05T09:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-05T09:30:00Z", "timeZone": "UTC"},
                "recurrence": ["RRULE:FREQ=DAILY;INTERVAL=1;UNTIL=20260607"],
            },
            max_occurrences=10,
        )

        self.assertTrue(result["synced"])
        self.assertTrue(result["task_created"])
        self.assertEqual(result["occurrences_total"], 3)

        task = Task.objects.get(google_event_id="google_series_123")
        self.assertEqual(task.frequency_type, "daily")
        self.assertEqual(task.recurrence_rule, "FREQ=DAILY;INTERVAL=1;UNTIL=20260607")

        occurrences = TaskOccurrence.objects.filter(task=task).order_by("scheduled_date")
        self.assertEqual(occurrences.count(), 3)
        first_occurrence = occurrences.first()
        expected_first = first_occurrence.scheduled_date.strftime("%Y%m%dT") + first_occurrence.scheduled_time.strftime("%H%M%SZ")
        self.assertEqual(first_occurrence.google_recurrence_id, expected_first)

        mapping = EventSyncMap.objects.get(user=self.user, google_event_id="google_series_123")
        self.assertTrue(mapping.is_recurring)
        self.assertEqual(mapping.local_task_id, task.id)

    def test_sync_google_recurring_to_app_updates_existing_task(self):
        task = Task.objects.create(
            user=self.user,
            title="Old Title",
            section="personal",
            status="pending",
            frequency_type="daily",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 7),
            start_time=datetime.strptime("09:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20260607",
            google_event_id="google_series_456",
            external_google_id=True,
        )

        result = sync_google_recurring_to_app(
            self.user,
            {
                "id": "google_series_456",
                "etag": "etag_series_456",
                "summary": "Updated Title",
                "description": "Now updated",
                "start": {"dateTime": "2026-06-05T09:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-05T10:00:00Z", "timeZone": "UTC"},
                "recurrence": ["RRULE:FREQ=DAILY;INTERVAL=2;UNTIL=20260609"],
            },
            max_occurrences=10,
        )

        self.assertTrue(result["synced"])
        self.assertFalse(result["task_created"])

        task.refresh_from_db()
        self.assertEqual(task.title, "Updated Title")
        self.assertEqual(task.frequency_interval, 2)
        self.assertEqual(task.recurrence_rule, "FREQ=DAILY;INTERVAL=2;UNTIL=20260609")
        self.assertEqual(TaskOccurrence.objects.filter(task=task).count(), 3)


class GoogleRecurringModificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="googlemodify",
            email="googlemodify@example.com",
        )

    def test_update_rrule_until_date_replaces_until(self):
        updated = _update_rrule_until_date("FREQ=DAILY;INTERVAL=2;UNTIL=20260609", date(2026, 6, 7))
        self.assertEqual(updated, "FREQ=DAILY;INTERVAL=2;UNTIL=20260607")

    def test_sync_google_recurring_change_updates_single_exception(self):
        task = Task.objects.create(
            user=self.user,
            title="Daily Task",
            section="personal",
            status="pending",
            frequency_type="daily",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 10),
            start_time=datetime.strptime("09:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20260610",
            google_event_id="root_series_1",
            external_google_id=True,
        )
        occurrence = TaskOccurrence.objects.create(
            task=task,
            scheduled_date=date(2026, 6, 6),
            scheduled_time=datetime.strptime("09:00:00", "%H:%M:%S").time(),
            status="pending",
            google_recurrence_id="20260606T090000Z",
        )

        result = sync_google_recurring_change(
            self.user,
            {
                "id": "instance_1",
                "recurringEventId": "root_series_1",
                "status": "confirmed",
                "etag": "etag_instance_1",
                "originalStartTime": {"dateTime": "2026-06-06T09:00:00Z"},
                "extendedProperties": {"private": {"app_status": "completed"}},
            },
        )

        self.assertTrue(result["synced"])
        occurrence.refresh_from_db()
        self.assertEqual(occurrence.status, "completed")
        self.assertEqual(occurrence.google_event_id, "instance_1")

    def test_sync_google_status_to_app_skips_stale_google_update(self):
        task = Task.objects.create(
            user=self.user,
            title="Stale Status Task",
            section="personal",
            status="pending",
            frequency_type="once",
            start_date=date(2026, 6, 5),
            start_time=datetime.strptime("09:00:00", "%H:%M:%S").time(),
            end_date=date(2026, 6, 5),
        )
        occurrence = TaskOccurrence.objects.create(
            task=task,
            scheduled_date=date(2026, 6, 5),
            scheduled_time=datetime.strptime("09:00:00", "%H:%M:%S").time(),
            status="pending",
        )
        EventSyncMap.objects.create(
            user=self.user,
            local_occurrence_id=occurrence.id,
            local_parent_type="task",
            local_task_id=task.id,
            google_event_id="google_stale_1",
            calendar_id="primary",
            last_local_updated_at=timezone.now(),
            last_google_updated_at=timezone.now() - timedelta(hours=2),
            is_deleted=False,
        )

        synced = sync_google_status_to_app_occurrences(
            self.user,
            "primary",
            [
                {
                    "id": "google_stale_1",
                    "status": "cancelled",
                    "updated": "2026-01-01T00:00:00Z",
                    "etag": "etag_stale",
                    "extendedProperties": {"private": {}},
                }
            ],
        )

        occurrence.refresh_from_db()
        self.assertEqual(synced, 0)
        self.assertEqual(occurrence.status, "pending")

    @patch("integrations.services.pytime.sleep")
    def test_google_request_json_retries_transient_network_error(self, mock_sleep):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return self.payload

        calls = {"count": 0}

        def fake_urlopen(request, timeout=25):
            calls["count"] += 1
            if calls["count"] == 1:
                raise urllib.error.URLError("temporary outage")
            return FakeResponse(b'{"ok": true}')

        with patch("integrations.services.urllib.request.urlopen", side_effect=fake_urlopen):
            result = _google_request_json_with_retry("GET", "https://example.com", "token")

        self.assertEqual(result["ok"], True)
        self.assertEqual(calls["count"], 2)
        self.assertTrue(mock_sleep.called)

    @patch("integrations.services.create_recurring_google_event")
    def test_delete_task_occurrence_in_app_for_future_updates_rrule(self, mock_create):
        mock_create.return_value = {"created": True, "action": "update", "google_event_id": "root_series_2"}
        task = Task.objects.create(
            user=self.user,
            title="Daily Task",
            section="personal",
            status="pending",
            frequency_type="daily",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 10),
            start_time=datetime.strptime("09:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20260610",
            google_event_id="root_series_2",
        )
        occ1 = TaskOccurrence.objects.create(task=task, scheduled_date=date(2026, 6, 6), scheduled_time=datetime.strptime("09:00:00", "%H:%M:%S").time(), status="pending")
        occ2 = TaskOccurrence.objects.create(task=task, scheduled_date=date(2026, 6, 7), scheduled_time=datetime.strptime("09:00:00", "%H:%M:%S").time(), status="pending")

        result = delete_task_occurrence_in_app(occ1, delete_all_future=True)

        self.assertTrue(result["deleted"])
        task.refresh_from_db()
        occ1.refresh_from_db()
        occ2.refresh_from_db()
        self.assertEqual(task.recurrence_rule, "FREQ=DAILY;INTERVAL=1;UNTIL=20260605")
        self.assertEqual(occ1.status, "skipped")
        self.assertEqual(occ2.status, "skipped")
        self.assertTrue(mock_create.called)


class HabitRecurringSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="habitsync",
            email="habitsync@example.com",
        )
        self.connection = GoogleCalendarConnection.objects.create(
            user=self.user,
            email="habitsync@gmail.com",
            google_sub="google_habit_123",
            refresh_token="encrypted_refresh_token",
            access_token="encrypted_access_token",
            token_expiry=timezone.now(),
            scope="calendar",
            is_active=True,
        )

    @patch('integrations.services._google_request_json_with_retry')
    @patch('integrations.services._google_request_json')
    @patch('integrations.services.ensure_valid_access_token')
    def test_create_recurring_google_event_for_habit(self, mock_token, mock_request, mock_retry_request):
        mock_token.return_value = "access_token"
        mock_request.return_value = {"id": "habit_google_event_1", "etag": "habit_etag_1"}
        mock_retry_request.return_value = {"id": "habit_google_event_1", "etag": "habit_etag_1"}

        habit = Habit.objects.create(
            user=self.user,
            title="Morning Walk",
            section="personal",
            status="active",
            frequency_type="daily",
            frequency_interval=1,
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 10),
            start_time=datetime.strptime("06:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20260610",
        )

        result = create_recurring_google_event(self.user, habit)

        self.assertTrue(result["created"])
        self.assertEqual(result["parent_type"], "habit")
        payload = mock_request.call_args[0][3]
        self.assertEqual(payload["extendedProperties"]["private"]["app_type"], "habit")

    def test_sync_google_recurring_to_app_updates_existing_habit(self):
        habit = Habit.objects.create(
            user=self.user,
            title="Old Habit",
            section="personal",
            status="active",
            frequency_type="daily",
            start_date=date(2026, 6, 5),
            end_date=date(2026, 6, 7),
            start_time=datetime.strptime("06:00:00", "%H:%M:%S").time(),
            recurrence_rule="FREQ=DAILY;INTERVAL=1;UNTIL=20260607",
            google_event_id="habit_series_456",
            external_google_id=True,
        )

        result = sync_google_recurring_to_app(
            self.user,
            {
                "id": "habit_series_456",
                "etag": "etag_habit_series_456",
                "summary": "Updated Habit",
                "description": "Habit from Google",
                "start": {"dateTime": "2026-06-05T06:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-05T06:30:00Z", "timeZone": "UTC"},
                "recurrence": ["RRULE:FREQ=DAILY;INTERVAL=1;UNTIL=20260607"],
                "extendedProperties": {"private": {"app_type": "habit", "app_id": str(habit.id)}},
            },
            max_occurrences=10,
        )

        self.assertTrue(result["synced"])
        self.assertEqual(result["parent_type"], "habit")
        habit.refresh_from_db()
        self.assertEqual(habit.title, "Updated Habit")
        self.assertEqual(TaskOccurrence.objects.filter(habit=habit).count(), 3)

    def test_sync_external_google_single_event_with_reminders(self):
        result = sync_external_google_event_to_app(
            self.user,
            {
                "id": "google_external_single",
                "etag": "etag_external_single",
                "summary": "Doctor Visit",
                "description": "External calendar entry",
                "start": {"dateTime": "2026-06-08T10:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-08T11:00:00Z", "timeZone": "UTC"},
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 15},
                        {"method": "email", "minutes": 60},
                    ],
                },
            },
        )

        self.assertTrue(result["synced"])
        task = Task.objects.get(google_event_id="google_external_single")
        self.assertTrue(task.reminder_enabled)
        self.assertEqual(len(task.reminder_offset), 2)
        occurrence = TaskOccurrence.objects.get(task=task)
        self.assertEqual(occurrence.reminders.count(), 2)

    def test_sync_external_google_all_day_event_creates_task(self):
        result = sync_external_google_event_to_app(
            self.user,
            {
                "id": "google_all_day_event",
                "etag": "etag_all_day",
                "summary": "All Day Conference",
                "description": "All-day external event",
                "start": {"date": "2026-06-08"},
                "end": {"date": "2026-06-09"},
            },
        )

        self.assertTrue(result["synced"])
        task = Task.objects.get(google_event_id="google_all_day_event")
        self.assertEqual(task.start_date, date(2026, 6, 8))
        occurrence = TaskOccurrence.objects.get(task=task)
        self.assertEqual(occurrence.scheduled_date, date(2026, 6, 8))

    def test_sync_external_google_single_event_cancellation_marks_deleted(self):
        created = sync_external_google_event_to_app(
            self.user,
            {
                "id": "google_external_cancel",
                "etag": "etag_external_cancel",
                "summary": "Cancelled Event",
                "description": "Create then cancel",
                "start": {"dateTime": "2026-06-08T12:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-08T13:00:00Z", "timeZone": "UTC"},
            },
        )

        self.assertTrue(created["synced"])

        cancelled = sync_external_google_event_to_app(
            self.user,
            {
                "id": "google_external_cancel",
                "etag": "etag_external_cancel_2",
                "status": "cancelled",
                "summary": "Cancelled Event",
                "start": {"dateTime": "2026-06-08T12:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-06-08T13:00:00Z", "timeZone": "UTC"},
            },
        )

        self.assertTrue(cancelled["synced"])
        task = Task.objects.get(google_event_id="google_external_cancel")
        self.assertTrue(task.is_deleted)
        occurrence = TaskOccurrence.objects.get(task=task)
        self.assertTrue(occurrence.is_deleted)
        self.assertEqual(occurrence.status, "skipped")
    
    def test_distinguish_recurring_from_single(self):
        """Test that we can distinguish recurring events from single events."""
        task_id = uuid.uuid4()
        occurrence_id = uuid.uuid4()
        
        # Single event
        single = EventSyncMap.objects.create(
            user=self.user,
            local_occurrence_id=occurrence_id,
            local_parent_type="task",
            google_event_id="google_single",
            calendar_id="primary",
            is_recurring=False,
        )
        
        # Recurring event
        recurring = EventSyncMap.objects.create(
            user=self.user,
            local_task_id=task_id,
            local_occurrence_id=task_id,
            local_parent_type="task",
            google_event_id="google_recurring",
            calendar_id="primary",
            is_recurring=True,
            recurrence_rule="FREQ=WEEKLY;BYDAY=MO,WE,FR",
        )
        
        # Query recurring events
        recurring_maps = EventSyncMap.objects.filter(is_recurring=True)
        self.assertEqual(recurring_maps.count(), 1)
        self.assertEqual(recurring_maps.first().google_event_id, "google_recurring")
        
        # Query single events
        single_maps = EventSyncMap.objects.filter(is_recurring=False)
        self.assertGreaterEqual(single_maps.count(), 1)
        logger.info("Successfully distinguished recurring and single event mappings")
