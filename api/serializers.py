from rest_framework import serializers
from authentication.models import User
from .models import Student, Section, AttendanceLog, ClassSession
from rest_framework.validators import UniqueTogetherValidator


class TeacherSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'username', 'email']



class StudentSerializer(serializers.ModelSerializer):
    """Lightweight serializer for nested enrolled students."""
    class Meta:
        model = Student
        fields = ['id', 'name', 'registration_number', 'department', 'created_at']
        read_only_fields = fields




class SectionReadSerializer(serializers.ModelSerializer):
    """Used for GET requests to display rich, nested data."""
    teacher = TeacherSerializer(read_only=True)
    enrolled_students = StudentSerializer(many=True, read_only=True)

    class Meta:
        model = Section
        fields = ['id', 'course_code', 'course_name', 'teacher', 'name', 'enrolled_students']




class SectionWriteSerializer(serializers.ModelSerializer):
    # Automatically pulls the logged-in user from the request context
    teacher = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Section
        fields = ['course_code', 'course_name', 'name', 'teacher']
        extra_kwargs = {
            'course_code': {'required': True, 'allow_blank': False},
            'course_name': {'required': True, 'allow_blank': False},
            'name': {'required': True, 'allow_blank': False},
        }
        validators = [
            UniqueTogetherValidator(
                queryset=Section.objects.all(),
                fields=['teacher', 'course_code', 'course_name', 'name'],
                message="You have already created a section with this course code, course name, and section name."
            )
        ]




class ClassSessionSerializer(serializers.ModelSerializer):
    section = SectionReadSerializer(read_only=True)

    class Meta:
        model = ClassSession
        fields = ['id', 'section', 'start_time', 'end_time', 'is_active']






class AttendanceLogSerializer(serializers.ModelSerializer):
    student = StudentSerializer(read_only=True)

    class Meta:
        model = AttendanceLog
        fields = ['id', 'session', 'student', 'timestamp']