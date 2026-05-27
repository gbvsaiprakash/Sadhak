from rest_framework import serializers
from tracker.models.notes_and_diary import Notes, NoteContent, DiaryPage

class NoteContentSerializer(serializers.ModelSerializer):
    # Explicit read-only source mapping to match frontend "notes_id" property expectation
    notes_id = serializers.ReadOnlyField(source="notes.id")
    
    class Meta:
        model = NoteContent
        fields = ["id", "notes_id", "content", "created_at", "updated_at"]


class DiaryPageSerializer(serializers.ModelSerializer):
    diary_id = serializers.ReadOnlyField(source="notes.id")

    class Meta:
        model = DiaryPage
        fields = ["id", "diary_id", "date", "content", "created_at", "updated_at"]


class NotesSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notes
        fields = ["id", "type", "title", "date", "tags", "is_pinned", "description", "section", "created_at", "updated_at", "reminder_id"]
        read_only_fields = ["user", "created_at", "updated_at"]


class NotesDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notes
        fields = ["id", "user", "type", "title", "date", "tags", "is_pinned", "description", "section", "created_at", "updated_at", "reminder_id"]
