import os
from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sadhak.settings")

app = Celery("sadhak")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
app.conf.imports = ("sadhak_base.tasks", "expenses.tasks")
