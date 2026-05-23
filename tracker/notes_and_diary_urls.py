# Notes and Diary
from django.urls import path

from tracker.views.notes_and_diary import (
    NotesAPIView,
    NotesDetailAPIView,
    NoteContentAPIView,
    DiaryPageAPIView,
)

urlpatterns = [
    path("", NotesAPIView.as_view(), name="tracker-notes"),
    path("<uuid:pk>/", NotesDetailAPIView.as_view(), name="tracker-notes-detail"),
    path("<uuid:pk>/content/", NoteContentAPIView.as_view(), name="tracker-note-content"),
    path("<uuid:pk>/pages/", DiaryPageAPIView.as_view(), name="tracker-diary"),
    path("<uuid:pk>/pages/<uuid:page_pk>/", DiaryPageAPIView.as_view(), name="tracker-diary-page"),
]