from django.core.management.base import BaseCommand
from integrations.models import GoogleCalendarConnection, EventSyncMap
from user_management.models import User
from tracker.models import TaskOccurrence


class Command(BaseCommand):
    help = "Debug Google Calendar sync status"

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            help='Username to check (default: first user)',
            default=None
        )

    def handle(self, *args, **options):
        username = options['username']
        
        if username:
            user = User.objects.filter(username=username, is_deleted=False).first()
            if not user:
                self.stdout.write(self.style.ERROR(f'User "{username}" not found'))
                return
        else:
            user = User.objects.filter(is_deleted=False).first()
            if not user:
                self.stdout.write(self.style.ERROR('No users found'))
                return

        self.stdout.write(self.style.SUCCESS(f'\n=== Google Calendar Sync Status for User: {user.username} ===\n'))

        # Check connection
        connection = GoogleCalendarConnection.objects.filter(user=user, is_active=True).first()
        if connection:
            self.stdout.write(self.style.SUCCESS(f'✓ Google Calendar Connected'))
            self.stdout.write(f'  Email: {connection.email}')
            self.stdout.write(f'  Active: {connection.is_active}')
            self.stdout.write(f'  Has Refresh Token: {bool(connection.refresh_token)}')
            self.stdout.write(f'  Token Expiry: {connection.token_expiry}')
        else:
            self.stdout.write(self.style.ERROR('✗ Google Calendar NOT Connected'))
            return

        # Check watch
        watches = user.google_calendar_watches.filter(is_active=True)
        self.stdout.write(f'\nWatches: {watches.count()}')
        for watch in watches:
            self.stdout.write(f'  - Calendar: {watch.calendar_id}, Active: {watch.is_active}')

        # Check synced occurrences
        maps = EventSyncMap.objects.filter(user=user, is_deleted=False)
        self.stdout.write(f'\nSynced Occurrences: {maps.count()}')
        for m in maps[:10]:  # Show first 10
            try:
                occ = TaskOccurrence.objects.get(id=m.local_occurrence_id)
                self.stdout.write(
                    f'  - {occ.id} (status: {occ.status}) → Google {m.google_event_id} '
                    f'(updated: {m.last_local_updated_at})'
                )
            except TaskOccurrence.DoesNotExist:
                self.stdout.write(f'  - {m.local_occurrence_id} (DELETED OCCURRENCE) → {m.google_event_id}')

        # Check pending occurrences
        self.stdout.write(f'\nPending Occurrences (not synced):')
        from datetime import date
        pending_occs = TaskOccurrence.objects.filter(
            task__user=user,
            scheduled_date__gte=date.today(),
            is_deleted=False
        ).exclude(id__in=maps.values_list('local_occurrence_id', flat=True))
        
        if pending_occs.exists():
            self.stdout.write(self.style.WARNING(f'  {pending_occs.count()} occurrences not yet synced'))
            for occ in pending_occs[:5]:
                self.stdout.write(f'    - {occ.id} ({occ.task.title if occ.task else occ.habit.title})')
        else:
            self.stdout.write(f'  All occurrences synced!')

        self.stdout.write(self.style.SUCCESS('\n✓ Debug complete\n'))
