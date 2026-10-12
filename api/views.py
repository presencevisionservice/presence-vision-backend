from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from django.shortcuts import get_object_or_404
from django.utils import timezone
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from rest_framework.permissions import IsAuthenticated
from authentication.permissions import IsStaff
import json


from .models import Section, ClassSession, Student, AttendanceLog
from .serializers import SectionReadSerializer, SectionWriteSerializer, AttendanceLogSerializer, ClassSessionSerializer


from .redis import redis_client, REDIS_QUEUE_NAME


class AttendanceWebhookView(APIView):
    """
    Phase 3: Receives successful face matches from worker.py.
    Validates section roster membership, records log, and broadcasts to WebSocket.
    """
    def post(self, request):
        session_id = request.data.get('session_id')
        student_uuid = request.data.get('uuid')

        if not session_id or not student_uuid:
            return Response(
                {"error": "session_id and uuid are required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # 1. Fetch active session and student profile
        session = get_object_or_404(ClassSession, id=session_id, is_active=True)
        student = get_object_or_404(Student, id=student_uuid)

        # 2. Roster Validation: Check if student belongs to this Section
        is_enrolled = session.section.enrolled_students.filter(id=student.id).exists()

        if not is_enrolled:
            # Broadcast an alert to the React UI about an unregistered attendee
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"session_{session.id}",
                {
                    "type": "attendance_event",
                    "status": "warning",
                    "student_name": student.name,
                    "registration_number": student.registration_number,
                    "message": "Student recognized but NOT enrolled in this section."
                }
            )
            return Response(
                {"status": "rejected", "reason": "Student not enrolled in section"}, 
                status=status.HTTP_200_OK
            )

        # 3. Save Attendance Log (unique_together prevents duplicate DB entries)
        log, created = AttendanceLog.objects.get_or_create(
            session=session,
            student=student
        )

        if created:
            # 4. Broadcast success update to React frontend via Django Channels
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f"session_{session.id}",
                {
                    "type": "attendance_event",
                    "status": "success",
                    "student_name": student.name,
                    "registration_number": student.registration_number,
                    "department": student.department,
                    "timestamp": log.timestamp.isoformat()
                }
            )
            return Response(
                {"status": "logged", "student": student.name}, 
                status=status.HTTP_201_CREATED
            )

        return Response(
            {"status": "already_logged", "student": student.name}, 
            status=status.HTTP_200_OK
        )




class TeacherEnrollTaskView(APIView):
    
    def post(self, request):
        session_id = request.data.get('session_id')
        student_name = request.data.get('student_name')
        registration_number = request.data.get('registration_number')
        department = request.data.get('department')
        frame_data = request.data.get('frame_data')

        if not session_id or not frame_data or not student_name or not registration_number:
            return Response(
                {"error": "session_id, student_name, registration_number, and frame_data are required."},
                status=status.HTTP_400_BAD_REQUEST
            )

        task_payload = {
            "action": "enroll",
            "session_id": session_id,
            "student_name": student_name,
            "registration_number": registration_number,
            "department": department or "General",
            "frame_data": frame_data
        }

        try:
            redis_client.rpush(REDIS_QUEUE_NAME, json.dumps(task_payload))

            # STRICT LIMIT: Keep only the latest 10 items. 
            # If worker is offline, old frames are immediately dropped to protect your 30MB limit.
            redis_client.ltrim(REDIS_QUEUE_NAME, -10, -1)
            
            return Response(
                {"status": "queued", "message": "Enrollment task queued for AI vision worker."},
                status=status.HTTP_202_ACCEPTED
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to push task to Redis queue: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )




class EnrollmentWebhookView(APIView):
    """
    Phase 2: Receives the final result of the enrollment task from worker.py.
    Saves the student to PostgreSQL if successful, links to Section, and broadcasts via WebSocket.
    """
    def post(self, request):
        session_id = request.data.get('session_id')
        status_flag = request.data.get('status')
        cause = request.data.get('cause')
        message = request.data.get('message')

        if not session_id:
            return Response(
                {"error": "session_id is required for WebSocket broadcasting"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        channel_layer = get_channel_layer()
        session = ClassSession.objects.filter(id=session_id).first()

        # Handle Worker Failure (Spoof, Duplicate, No Face, etc.)
        if status_flag == "failed":
            # Check if failure is due to student already existing in the vector database
            if cause == "already_in_vector_db":
                existing_uuid = request.data.get('existing_uuid')
                
                if not existing_uuid:
                    return Response(
                        {"error": "existing_uuid is required for already_in_vector_db cause"},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Fetch the student from PostgreSQL using the existing UUID
                student = Student.objects.filter(id=existing_uuid).first()
                if not student:
                    return Response(
                        {"error": "Student found in vector database but missing from PostgreSQL record."},
                        status=status.HTTP_404_NOT_FOUND
                    )

                # Check if the student is already enrolled in the current section
                already_enrolled = False
                if session and session.section:
                    already_enrolled = session.section.enrolled_students.filter(id=student.id).exists()

                if already_enrolled:
                    # Broadcast that the student is already enrolled in this section
                    async_to_sync(channel_layer.group_send)(
                        f"session_{session_id}",
                        {
                            "type": "enrollment_event",
                            "status": "failed",
                            "cause": "already_enrolled_in_section",
                            "message": f"Student {student.name} ({student.registration_number}) is already enrolled in this section.",
                            "student_data": {
                                "uuid": str(student.id),
                                "name": student.name,
                                "registration_number": student.registration_number,
                                "department": student.department
                            }
                        }
                    )
                    return Response(
                        {"status": "already_enrolled", "message": f"Student {student.name} is already enrolled in the section."},
                        status=status.HTTP_200_OK
                    )
                else:
                    # Enroll the existing student into the current section roster
                    if session and session.section:
                        session.section.enrolled_students.add(student)

                    # Broadcast success message for enrolling an existing student into the new section
                    async_to_sync(channel_layer.group_send)(
                        f"session_{session_id}",
                        {
                            "type": "enrollment_event",
                            "status": "success",
                            "message": f"Existing student {student.name} successfully enrolled in the section.",
                            "student_data": {
                                "uuid": str(student.id),
                                "name": student.name,
                                "registration_number": student.registration_number,
                                "department": student.department
                            }
                        }
                    )
                    return Response(
                        {"status": "enrolled", "student": student.name},
                        status=status.HTTP_201_CREATED
                    )

            # Handle other general failures (no_face, many_faces, invalid_image, etc.)
            async_to_sync(channel_layer.group_send)(
                f"session_{session_id}",
                {
                    "type": "enrollment_event",
                    "status": "failed",
                    "cause": cause,
                    "message": message
                }
            )
            return Response({"status": "relayed_failure"}, status=status.HTTP_200_OK)

        # Handle Worker Success (New face passed checks and is stored in Qdrant)
        worker_data = request.data.get('data', {})
        student_id = worker_data.get('uuid')

        if not student_id:
            return Response(
                {"error": "Student UUID is required in worker data"}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # Save the student to PostgreSQL using the exact UUID from Qdrant
        student, created = Student.objects.get_or_create(
            id=student_id,
            defaults={
                'name': worker_data.get('student_name'),
                'registration_number': worker_data.get('registration_number'),
                'department': worker_data.get('department')
            }
        )

        # Automatically associate student with the current class section roster
        if session and session.section:
            session.section.enrolled_students.add(student)

        # Broadcast success to the React frontend
        async_to_sync(channel_layer.group_send)(
            f"session_{session_id}",
            {
                "type": "enrollment_event",
                "status": "success",
                "message": message,
                "student_data": {
                    "uuid": str(student.id),
                    "name": student.name,
                    "registration_number": student.registration_number,
                    "department": student.department
                }
            }
        )

        return Response(
            {"status": "enrolled", "student": student.name}, 
            status=status.HTTP_201_CREATED
        )




class GetTeacherDetails(APIView):
    
    permission_classes = [IsAuthenticated, IsStaff]

    def get(self, request):
        user_details = {
            "id": request.user.id,
            "username": request.user.username,
            "email": request.user.email,
        }
        return Response(user_details, status=status.HTTP_200_OK)





class TeacherSectionListCreateView(generics.ListCreateAPIView):
    """
    GET: Returns a list of sections taught by the current user.
    POST: Creates a new section and assigns the current user as the teacher.
    """
    permission_classes = [IsAuthenticated, IsStaff]

    def get_queryset(self):
        # Secures the endpoint so teachers only see their own sections
        return Section.objects.filter(teacher=self.request.user)

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return SectionWriteSerializer
        return SectionReadSerializer

    def create(self, request, *args, **kwargs):
        # 1. Validate using SectionWriteSerializer (handles CurrentUserDefault + UniqueTogetherValidator)
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        section = serializer.save()

        # 2. Return the created object formatted with SectionReadSerializer
        read_serializer = SectionReadSerializer(section)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)



class TeacherSectionDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET: View a specific section.
    PUT/PATCH: Update a specific section.
    DELETE: Remove a specific section.
    """
    permission_classes = [IsAuthenticated, IsStaff]

    def get_queryset(self):
        # Ensures teachers can only edit/delete their own sections
        return Section.objects.filter(teacher=self.request.user)

    def get_serializer_class(self):
        if self.request.method in ['PUT', 'PATCH']:
            return SectionWriteSerializer
        return SectionReadSerializer




class StartClassSessionView(APIView):
    """
    Teacher creates a new active session for a specific section.
    Automatically deactivates any existing active session for that section.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        section_id = request.data.get('section_id')
        if not section_id:
            return Response({"error": "section_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        section = get_object_or_404(Section, id=section_id, teacher=request.user)

        # Close any existing active session for this section
        ClassSession.objects.filter(section=section, is_active=True).update(
            is_active=False,
            end_time=timezone.now()
        )

        session = ClassSession.objects.create(section=section, is_active=True)
        serializer = ClassSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_201_CREATED)





class EndClassSessionView(APIView):
    """
    Teacher stops an active class session.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, session_id):
        session = get_object_or_404(ClassSession, id=session_id, section__teacher=request.user, is_active=True)
        session.is_active = False
        session.end_time = timezone.now()
        session.save()

        serializer = ClassSessionSerializer(session)
        return Response(serializer.data, status=status.HTTP_200_OK)





class GetSessionLogsView(APIView):
    """
    Retrieves all recorded attendance entries for a given session.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, session_id):
        session = get_object_or_404(ClassSession, id=session_id, section__teacher=request.user)
        logs = AttendanceLog.objects.filter(session=session).select_related('student')
        serializer = AttendanceLogSerializer(logs, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)