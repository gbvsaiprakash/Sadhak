# # create serializers for notes and diary
# from rest_framework import serializers
# from tracker.models.notes_and_diary import Notes, NoteContent, DiaryPage

# class NoteContentSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = NoteContent
#         fields = ["id", "content", "created_at", "updated_at"]
    
#     # validate notes id exists    
#     def validate_notes_id(self, value):
#         if not Notes.objects.filter(id=value).exists():
#             raise serializers.ValidationError("Notes with given id does not exist")

# class NotesSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Notes
#         fields = ["id", "type", "title", "date", "tags", "is_pinned", "description", "section", "created_at", "updated_at"]
#         read_only_fields = ["user", "created_at", "updated_at"]

# class NotesDetailSerializer(serializers.ModelSerializer):

#     class Meta:
#         model = Notes
#         fields = ["id", "user", "type", "title", "date", "tags", "is_pinned", "description", "section", "created_at", "updated_at"]
    
# class DiaryPageSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = DiaryPage
#         fields = ["id", "diary_id", "date", "content", "created_at", "updated_at"]
    
#     # validate diary id exists and is of type diary
#     def validate_diary_id(self, value):
#         if not Notes.objects.filter(id=value, type="diary").exists():
#             raise serializers.ValidationError("Diary with given id does not exist")
#         return value
    
#     # validate unique date for a given diary id
#     def validate(self, data):
#         diary_id = data.get("diary_id")
#         date = data.get("date")
#         if DiaryPage.objects.filter(diary_id=diary_id, date=date).exists():
#             raise serializers.ValidationError("Diary page for given date already exists")
#         return data

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
        fields = ["id", "type", "title", "date", "tags", "is_pinned", "description", "section", "created_at", "updated_at"]
        read_only_fields = ["user", "created_at", "updated_at"]


class NotesDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notes
        fields = ["id", "user", "type", "title", "date", "tags", "is_pinned", "description", "section", "created_at", "updated_at"]
