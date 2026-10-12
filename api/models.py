import uuid
from django.db import models
from django.conf import settings


# # 1. Global Identity Pool
class Student(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False) # Syncs with Qdrant
    name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=50, unique=True)
    department = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.registration_number})"





# # 3. Specific Teacher's Class
class Section(models.Model):
    course_code = models.CharField(max_length=20)
    course_name = models.CharField(max_length=100)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='taught_sections')
    name = models.CharField(max_length=100)
    enrolled_students = models.ManyToManyField(Student, related_name='enrolled_sections', blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['teacher', 'course_code', 'course_name', 'name'],
                name='unique_section_per_teacher'
            )
        ]

    def __str__(self):
        return f"{self.course_code} | {self.course_name} | {self.name} ({self.teacher.username})"


# # 4. A Single Day's Lecture
class ClassSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='sessions')
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.section.name} - {self.start_time.strftime('%Y-%m-%d')}"



# 5. The Actual Attendance Record
class AttendanceLog(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name='logs')
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name='attendance')
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        # A student can only be marked present once per specific lecture
        unique_together = ('session', 'student')