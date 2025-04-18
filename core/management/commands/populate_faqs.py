from django.core.management.base import BaseCommand
from core.models import FAQItem


class Command(BaseCommand):
    help = 'Populates the database with sample FAQ items'

    def handle(self, *args, **kwargs):
        faqs = [
            {
                'question': 'How do I create a playlist?',
                'answer': 'To create a playlist, go to your dashboard and click on the "Create New Playlist" button. Enter a name for your playlist and optionally add a description. Click "Create Playlist" to save it.'
            },
            {
                'question': 'Can I use MeloMatch offline?',
                'answer': 'Currently, MeloMatch requires an internet connection to stream music and access your playlists. We\'re working on an offline mode for premium users, which will be available in a future update.'
            },
            {
                'question': 'How do I upgrade my subscription?',
                'answer': 'To upgrade your subscription, go to your account settings and click on "Subscription Plans". Choose the plan that best suits your needs and follow the payment instructions to complete the upgrade.'
            },
            {
                'question': 'What audio quality does MeloMatch offer?',
                'answer': 'MeloMatch offers streaming quality up to 320kbps for premium users. Free users can enjoy music at 128kbps. The actual quality may vary depending on your internet connection and device capabilities.'
            },
            {
                'question': 'How do I add songs to my playlist?',
                'answer': 'To add songs to your playlist, search for the desired track or browse recommendations. Click on the "Add to Playlist" button next to the song, then select the playlist you want to add it to.'
            },
        ]

        for faq in faqs:
            FAQItem.objects.create(question=faq['question'], answer=faq['answer'])

        self.stdout.write(self.style.SUCCESS('Successfully populated FAQ items'))
