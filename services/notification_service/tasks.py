from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3, default_retry_delay=60) # Added bind, retries
def send_email_task(self, subject, message, recipient_list, from_email=None, html_message=None):
    if not from_email:
        from_email = settings.DEFAULT_FROM_EMAIL
    try:
        send_mail(
            subject,
            message,
            from_email,
            recipient_list,
            html_message=html_message,
            fail_silently=False,
        )
        logger.info(f"Email sent successfully to {recipient_list} with subject: {subject}")
        return f"Email sent to {recipient_list}"
    except Exception as e:
        logger.error(f"Error sending email to {recipient_list} with subject {subject}: {e}", exc_info=True)
        # Retry the task if an exception occurs
        # self.retry(exc=e, countdown=int(self.request.retries * 2) * 60 + 60) # Exponential backoff example
        raise self.retry(exc=e, countdown=60 * (self.request.retries + 1)) # Simple incremental backoff
