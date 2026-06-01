from django.apps import AppConfig


class IntegrationsConfig(AppConfig):
    name = 'integrations'

    def ready(self):
        """Import signals when Django is ready."""
        import integrations.signals  # noqa
