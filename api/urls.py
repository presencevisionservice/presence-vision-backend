# urls.py
from django.urls import path
from . import views

urlpatterns = [
    
    # Teacher & Section Management
    path("teacher/details/", views.GetTeacherDetails.as_view(), name="teacher-details"),


    # GET to list, POST to create
    path('teacher/sections/', views.TeacherSectionListCreateView.as_view(), name='teacher-sections-list'),
    
    # GET to retrieve, PUT/PATCH to update, DELETE to remove
    path('teacher/sections/<int:pk>/', views.TeacherSectionDetailView.as_view(), name='teacher-sections-detail'),
    
    
    
    # Class Session Lifecycle
    path("sessions/start/", views.StartClassSessionView.as_view(), name="session-start"),
    path("sessions/<uuid:session_id>/end/", views.EndClassSessionView.as_view(), name="session-end"),
    path("sessions/<uuid:session_id>/logs/", views.GetSessionLogsView.as_view(), name="session-logs"),

    
    
    # Vision Worker Webhooks
    path("enroll/", views.TeacherEnrollTaskView.as_view(), name="teacher-enroll-task"),
    path("enrollment_webhook/", views.EnrollmentWebhookView.as_view(), name="enrollment-webhook"),
    path("attendance_webhook/", views.AttendanceWebhookView.as_view(), name="attendance-webhook"),
]