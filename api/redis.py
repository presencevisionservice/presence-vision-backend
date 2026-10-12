import redis
from django.conf import settings


redis_client = redis.from_url(settings.REDIS_URL)
REDIS_QUEUE_NAME = 'frame_queue'