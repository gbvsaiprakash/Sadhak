from django.core.management.base import BaseCommand
from integrations.services import sync_parent_action_to_google
from tracker.models import TaskOccurrence
from user_management.models import User


class Command(BaseCommand):
    help = "Manually test syncing an occurrence to Google Calendar"

    def add_arguments(self, parser):
        parser.add_argument(
            'occurrence_id',
            type=str,
            help='Occurrence ID (UUID) to sync'
        )
        parser.add_argument(
            '--username',
            type=str,
            help='Username (if not provided, derives from occurrence)',
            default=None
        )
        parser.add_argument(
            '--action',
            type=str,
            choices=['create', 'update', 'delete'],
            help='Action to perform',
            default='update'
        )

    def handle(self, *args, **options):
        occurrence_id = options['occurrence_id']
        action = options['action']

        try:
            occurrence = TaskOccurrence.objects.get(id=occurrence_id)
        except TaskOccurrence.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Occurrence {occurrence_id} not found'))
            return

        parent = occurrence.task or occurrence.habit
        user = getattr(parent, 'user', None)

        if not user:
            self.stdout.write(self.style.ERROR('No user found for this occurrence'))
            return

        self.stdout.write(f'\n=== Testing Google Calendar Sync ===\n')
        self.stdout.write(f'Occurrence: {occurrence_id}')
        self.stdout.write(f'User: {user.username}')
        self.stdout.write(f'Action: {action}')
        self.stdout.write(f'Status: {occurrence.status}')
        self.stdout.write(f'Parent: {parent.title}\n')

        try:
            result = sync_parent_action_to_google(user, parent, action=action, occurrence=occurrence)
            self.stdout.write(self.style.SUCCESS(f'Sync Result:'))
            for key, value in result.items():
                self.stdout.write(f'  {key}: {value}')
            
            if result.get('pushed'):
                self.stdout.write(self.style.SUCCESS('\n✓ Successfully synced to Google Calendar!'))
            else:
                reason = result.get('reason', 'unknown')
                self.stdout.write(self.style.WARNING(f'\n✗ Sync skipped: {reason}'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Sync failed: {str(e)}'))
            import traceback
            traceback.print_exc()

        self.stdout.write('')
