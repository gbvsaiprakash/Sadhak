# from rest_framework import status
# from rest_framework.response import Response
# from rest_framework.views import APIView
# from django.shortcuts import get_object_or_404
# from tracker.models.notes_and_diary import Notes, NoteContent, DiaryPage
# from tracker.serializers.notes_and_diary import (
#     NoteContentSerializer,
#     DiaryPageSerializer,
#     NotesSerializer,
#     NotesDetailSerializer,
# )
# from user_management.views import AuthenticatedAPIView

# class NotesAPIView(AuthenticatedAPIView):
#     """List all notes with optional filtering by section and type. Create a new note."""
    
#     def get_queryset(self):
#         if not self.request.user or self.request.user.is_anonymous:
#             return Notes.objects.none()
#         return Notes.objects.filter(user=self.request.user, is_deleted=False)
    
#     def get(self, request):
#         """GET /api/notes/ - Query params: section?, type?"""
#         notes = self.get_queryset()
        
#         note_type = request.query_params.get("type")
#         if note_type:
#             notes = notes.filter(type=note_type)
        
#         section = request.query_params.get("section")
#         if section:
#             notes = notes.filter(section=section)
        
#         serializer = NotesSerializer(notes, many=True)
#         return Response(serializer.data)
    
#     def post(self, request):
#         """POST /api/notes/ - Create a new note or diary"""
#         serializer = NotesSerializer(data=request.data)
#         print(serializer.initial_data)
#         if serializer.is_valid():
#             note = serializer.save(user=request.user)
#             return Response(NotesSerializer(note).data, status=status.HTTP_201_CREATED)
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# class NotesDetailAPIView(AuthenticatedAPIView):
#     """Get, update, or delete a single note."""
    
#     def get_note(self, user, pk):
#         return get_object_or_404(Notes, id=pk, user=user, is_deleted=False)
    
#     def get(self, request, pk):
#         """GET /api/notes/{id}/"""
#         note = self.get_note(request.user, pk)
#         serializer = NotesDetailSerializer(note)
#         return Response(serializer.data)
    
#     def patch(self, request, pk):
#         """PATCH /api/notes/{id}/ - Update note metadata"""
#         note = self.get_note(request.user, pk)
#         serializer = NotesSerializer(note, data=request.data, partial=True)
#         if serializer.is_valid():
#             serializer.save()
#             return Response(NotesSerializer(note).data)
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
#     def delete(self, request, pk):
#         """DELETE /api/notes/{id}/"""
#         note = self.get_note(request.user, pk)
#         note.is_deleted = True
#         note.save()
#         return Response(status=status.HTTP_204_NO_CONTENT)


# class NoteContentAPIView(AuthenticatedAPIView):
#     """Get or save note content."""
    
#     def get_note(self, user, pk):
#         return get_object_or_404(Notes, id=pk, user=user, is_deleted=False)
    
#     def get(self, request, pk):
#         """GET /api/notes/{notesId}/content/"""
#         note = self.get_note(request.user, pk)
#         print(note.objects)
#         try:
#             content = note.content
#             serializer = NoteContentSerializer(content)
#             return Response(serializer.data)
#         except NoteContent.DoesNotExist:
#             return Response({
#                 "id": None,
#                 "notes_id": str(note.id),
#                 "content": "",
#                 "created_at": None,
#                 "updated_at": None,
#             })
    
#     def post(self, request, pk):
#         """POST /api/notes/{notesId}/content/ - Create or update note content"""
#         note = self.get_note(request.user, pk)
#         content_data = request.data.get("content", "")
        
#         try:
#             content = note.content
#             content.content = content_data
#             content.save()
#         except NoteContent.DoesNotExist:
#             content = NoteContent.objects.create(notes=note, content=content_data)
        
#         serializer = NoteContentSerializer(content)
#         return Response(serializer.data)


# class DiaryPageAPIView(AuthenticatedAPIView):
#     """List diary pages, create diary page, get/update/delete specific diary page."""
    
#     def get_diary(self, user, pk):
#         return get_object_or_404(Notes, id=pk, user=user, type="diary", is_deleted=False)
    
#     def get_page(self, diary, page_pk):
#         return get_object_or_404(DiaryPage, id=page_pk, diary=diary)
    
#     def get(self, request, pk, page_pk=None):
#         """GET /api/notes/{diaryId}/pages/ or /api/notes/{diaryId}/pages/{pageId}/"""
#         diary = self.get_diary(request.user, pk)
        
#         if page_pk:
#             page = self.get_page(diary, page_pk)
#             serializer = DiaryPageSerializer(page)
#             return Response(serializer.data)
#         else:
#             pages = diary.diary_pages.all()
#             serializer = DiaryPageSerializer(pages, many=True)
#             return Response(serializer.data)
    
#     def post(self, request, pk, page_pk=None):
#         """POST /api/notes/{diaryId}/pages/ - Create a new diary page"""
#         diary = self.get_diary(request.user, pk)
#         serializer = DiaryPageSerializer(data=request.data)
#         if serializer.is_valid():
#             try:
#                 page = serializer.save(diary=diary)
#                 return Response(DiaryPageSerializer(page).data, status=status.HTTP_201_CREATED)
#             except Exception:
#                 return Response(
#                     {"detail": "A diary page already exists for this date"},
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
#     def patch(self, request, pk, page_pk=None):
#         """PATCH /api/notes/{diaryId}/pages/{pageId}/ - Update a diary page"""
#         if not page_pk:
#             return Response({"detail": "Page ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
#         diary = self.get_diary(request.user, pk)
#         page = self.get_page(diary, page_pk)
#         serializer = DiaryPageSerializer(page, data=request.data, partial=True)
#         if serializer.is_valid():
#             try:
#                 serializer.save()
#                 return Response(DiaryPageSerializer(page).data)
#             except Exception:
#                 return Response(
#                     {"detail": "A diary page already exists for this date"},
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
#     def delete(self, request, pk, page_pk=None):
#         """DELETE /api/notes/{diaryId}/pages/{pageId}/ - Delete a diary page"""
#         if not page_pk:
#             return Response({"detail": "Page ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
#         diary = self.get_diary(request.user, pk)
#         page = self.get_page(diary, page_pk)
#         page.delete()
#         return Response(status=status.HTTP_204_NO_CONTENT)

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404
from django.core.exceptions import ObjectDoesNotExist
from tracker.models.notes_and_diary import Notes, NoteContent, DiaryPage
from tracker.serializers.notes_and_diary import (
    NoteContentSerializer,
    DiaryPageSerializer,
    NotesSerializer,
    NotesDetailSerializer,
)
from user_management.views import AuthenticatedAPIView

class NotesAPIView(AuthenticatedAPIView):
    """List all notes with optional filtering by section and type. Create a new note."""
    
    def get_queryset(self):
        if not self.request.user or self.request.user.is_anonymous:
            return Notes.objects.none()
        return Notes.objects.filter(user=self.request.user, is_deleted=False)
    
    def get(self, request):
        """GET /api/notes/ - Query params: section?, type?"""
        notes = self.get_queryset()
        
        note_type = request.query_params.get("type")
        if note_type:
            notes = notes.filter(type=note_type)
        
        section = request.query_params.get("section")
        if section:
            notes = notes.filter(section=section)
        
        serializer = NotesSerializer(notes, many=True)
        return Response(serializer.data)
    
    def post(self, request):
        """POST /api/notes/ - Create a new note or diary"""
        serializer = NotesSerializer(data=request.data)
        if serializer.is_valid():
            note = serializer.save(user=request.user)
            return Response(NotesSerializer(note).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class NotesDetailAPIView(AuthenticatedAPIView):
    """Get, update, or delete a single note."""
    
    def get_note(self, user, pk):
        return get_object_or_404(Notes, id=pk, user=user, is_deleted=False)
    
    def get(self, request, pk):
        """GET /api/notes/{id}/"""
        note = self.get_note(request.user, pk)
        serializer = NotesDetailSerializer(note)
        return Response(serializer.data)
    
    def patch(self, request, pk):
        """PATCH /api/notes/{id}/ - Update note metadata"""
        note = self.get_note(request.user, pk)
        serializer = NotesSerializer(note, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(NotesSerializer(note).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def delete(self, request, pk):
        """DELETE /api/notes/{id}/"""
        note = self.get_note(request.user, pk)
        note.is_deleted = True
        note.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NoteContentAPIView(AuthenticatedAPIView):
    """Get or save note content instead of parent metadata."""
    
    def get_note(self, user, pk):
        return get_object_or_404(Notes, id=pk, user=user, is_deleted=False)
    
    def get(self, request, pk):
        """GET /api/notes/{notesId}/content/"""
        note = self.get_note(request.user, pk)
        
        try:
            # Accessing missing OneToOne elements raises ObjectDoesNotExist
            content = NoteContent.objects.filter(notes_id=note.id).first()
            serializer = NoteContentSerializer(content)
            return Response(serializer.data)
        except Exception:
            return Response({
                "id": None,
                "notes_id": str(note.id),
                "content": "",
                "created_at": None,
                "updated_at": None,
            })
    
    def post(self, request, pk):
        """POST /api/notes/{notesId}/content/ - Create or update note content"""
        note = self.get_note(request.user, pk)
        content_data = request.data.get("content", "")
        
        # Uses atomic get_or_create to prevent missing relation race conditions
        content, created = NoteContent.objects.get_or_create(
            notes_id=note,
            defaults={"content": content_data}
        )
        
        if not created:
            content.content = content_data
            content.save()
            
        serializer = NoteContentSerializer(content)
        return Response(serializer.data)


class DiaryPageAPIView(AuthenticatedAPIView):
    """List diary pages, create diary page, get/update/delete specific diary page."""
    
    def get_diary(self, user, pk):
        return get_object_or_404(Notes, id=pk, user=user, type="diary", is_deleted=False)
    
    def get_page(self, diary, page_pk):
        return get_object_or_404(DiaryPage, id=page_pk, diary_id=diary)
    
    def get(self, request, pk, page_pk=None):
        """GET /api/notes/{diaryId}/pages/ or /api/notes/{diaryId}/pages/{pageId}/"""
        diary = self.get_diary(request.user, pk)
        
        if page_pk:
            page = self.get_page(diary, page_pk)
            serializer = DiaryPageSerializer(page)
            return Response(serializer.data)
        else:
            pages = DiaryPage.objects.filter(diary_id=diary, is_deleted=False)
            serializer = DiaryPageSerializer(pages, many=True)
            return Response(serializer.data)
    
    def post(self, request, pk, page_pk=None):
        """POST /api/notes/{diaryId}/pages/ - Create a new diary page"""
        diary = self.get_diary(request.user, pk)
        serializer = DiaryPageSerializer(data=request.data)
        if serializer.is_valid():
            try:
                page = serializer.save(diary_id=diary)
                return Response(DiaryPageSerializer(page).data, status=status.HTTP_201_CREATED)
            except Exception:
                return Response(
                    {"detail": "A diary page already exists for this date"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def patch(self, request, pk, page_pk=None):
        """PATCH /api/notes/{diaryId}/pages/{pageId}/ - Update a diary page"""
        if not page_pk:
            return Response({"detail": "Page ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        diary = self.get_diary(request.user, pk)
        page = self.get_page(diary, page_pk)
        serializer = DiaryPageSerializer(page, data=request.data, partial=True)
        if serializer.is_valid():
            try:
                serializer.save()
                return Response(DiaryPageSerializer(page).data)
            except Exception:
                return Response(
                    {"detail": "A diary page already exists for this date"},
                    status=status.HTTP_400_BAD_REQUEST
                )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def delete(self, request, pk, page_pk=None):
        """DELETE /api/notes/{diaryId}/pages/{pageId}/ - Delete a diary page"""
        if not page_pk:
            return Response({"detail": "Page ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        diary = self.get_diary(request.user, pk)
        page = self.get_page(diary, page_pk)
        page.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
