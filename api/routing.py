from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # Maps ws://localhost:8000/ws/session/<uuid>/ to the AttendanceConsumer
    re_path(r'ws/attendance/(?P<session_id>[a-f0-9\-]+)/$', consumers.AttendanceConsumer.as_asgi()),
    re_path(r"ws/session/(?P<session_id>[0-9a-fA-F-]+)/$", consumers.SessionConsumer.as_asgi()),
]