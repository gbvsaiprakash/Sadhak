from django.contrib import admin

from tracker.models import Goal, Habit, Milestone, Task, TaskOccurrence


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "section", "status", "start_date", "expected_end_date")
    list_filter = ("section", "status")
    search_fields = ("title", "description")


@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ("title", "goal", "status", "start_date", "expected_achieved_date")
    list_filter = ("status",)
    search_fields = ("title", "description")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "section", "status", "frequency_type", "start_date", "end_date")
    list_filter = ("section", "status", "frequency_type")
    search_fields = ("title", "description")


@admin.register(Habit)
class HabitAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "section", "status", "frequency_type", "start_date", "end_date")
    list_filter = ("section", "status", "frequency_type")
    search_fields = ("title", "description")


@admin.register(TaskOccurrence)
class TaskOccurrenceAdmin(admin.ModelAdmin):
    list_display = ("id", "task", "habit", "scheduled_date", "scheduled_time", "status")
    list_filter = ("status", "scheduled_date")
