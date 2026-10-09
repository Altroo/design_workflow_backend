from celery import shared_task
from django.core.management import call_command


@shared_task(name="chat_ai.purge_history", ignore_result=True)
def purge_history():
    call_command("purge_ai_history")
