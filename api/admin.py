from django.contrib import admin
from . import models


# Register your models here.
admin.site.register(models.Student)
admin.site.register(models.Section)
admin.site.register(models.ClassSession)
admin.site.register(models.AttendanceLog)
