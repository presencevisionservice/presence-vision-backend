import json
import redis.asyncio as redis
from channels.generic.websocket import AsyncWebsocketConsumer


# Asynchronous Redis client for non-blocking frame ingestion
redis_client = redis.Redis(host='127.0.0.1', port=6379, db=0, decode_responses=True)


class AttendanceConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Extract session_id from URL route: ws/session/<session_id>/
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.room_group_name = f'session_{self.session_id}'

        # Join room group for this class session
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group upon disconnect
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    # ---------------------------------------------------------
    # Receive Frame from React -> Push to Redis Queue
    # ---------------------------------------------------------
    async def receive(self, text_data):
        try:
            # Inside consumers.py -> async def receive(self, text_data)
            data = json.loads(text_data)
            base64_frame = data.get('frame')

            if base64_frame:
                payload = {
                    "session_id": str(self.session_id),
                    "frame_data": base64_frame,
                    # Pass the extra fields from React to the Redis worker
                    "action": data.get('action', 'attendance'),
                    "student_name": data.get('student_name'),
                    "registration_number": data.get('registration_number'),
                    "department": data.get('department')
                }
                await redis_client.rpush("frame_queue", json.dumps(payload))

        except json.JSONDecodeError:
            pass # Silently ignore malformed frames

    # ---------------------------------------------------------
    # Broadcast Handler (Dispatched by AIWebhookView)
    # ---------------------------------------------------------
    async def attendance_event(self, event):
        """
        Relays success or warning events back to the React UI WebSocket.
        """
        await self.send(text_data=json.dumps({
            "type": "attendance_update",
            "status": event["status"],             # "success" or "warning"
            "student_name": event.get("student_name"),
            "registration_number": event.get("registration_number"),
            "department": event.get("department", ""),
            "timestamp": event.get("timestamp", ""),
            "message": event.get("message", "")
        }))
        



class SessionConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.session_id = self.scope['url_route']['kwargs']['session_id']
        self.room_group_name = f"session_{self.session_id}"

        # Join the session room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        # Leave the session room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    # This method matches the "type": "enrollment_event" sent by your webhook
    async def enrollment_event(self, event):
        await self.send(text_data=json.dumps({
            "type": "enrollment_event",
            "status": event.get("status"),
            "message": event.get("message"),
            "student_data": event.get("student_data")
        }))