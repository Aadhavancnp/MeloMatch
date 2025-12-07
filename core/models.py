from django.db import models


class FAQItem(models.Model):
    question = models.CharField(max_length=255, db_index=True)
    answer = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.question

    class Meta:
        ordering = ['-created_at']


class Contact(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(db_index=True)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)

    def __str__(self):
        return f"{self.subject} - {self.name}"

    class Meta:
        ordering = ['-created_at']
