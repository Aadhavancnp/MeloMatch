from django import forms
from django.contrib.auth import get_user_model

from .models import Playlist

User = get_user_model()


class PlaylistSettingsForm(forms.ModelForm):
    # Use ModelMultipleChoiceField for shared_with for better widget and validation
    shared_with = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(is_active=True).order_by('username'),
        # This should be filtered to only include relevant users
        # You might want to filter this further, e.g., exclude self, only friends/followers
        widget=forms.CheckboxSelectMultiple,  # Or Select2 widget for better UX with many users
        required=False,
        help_text="Select users to share this playlist with."
    )

    class Meta:
        model = Playlist
        fields = ['name', 'description', 'is_public', 'shared_with',
                  'image_url']  # Assuming image_url might be editable if it's not Spotify's
        # If image_url is strictly from Spotify, you might remove it or handle it differently.
        # For local cover uploads, you'd use ImageField and handle file upload.

        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
            'is_public': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            # image_url might need a FileInput if you allow direct uploads, or just text if it's a URL from Spotify
            'image_url': forms.URLInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        # Exclude the playlist owner from the 'shared_with' queryset if needed
        # For example, if you have the user instance passed to the form:
        # owner = kwargs.pop('owner', None)
        super().__init__(*args, **kwargs)
        # if owner:
        #     self.fields['shared_with'].queryset = User.objects.exclude(pk=owner.pk)

        # If the instance is available (i.e., editing an existing playlist)
        if self.instance and self.instance.user:
            self.fields['shared_with'].queryset = User.objects.exclude(pk=self.instance.user.pk)

        # If you want to allow users to clear the image_url (if it's a URLField from Spotify)
        # you might need custom handling or a ClearableFileInput if it were an ImageField.
        # For a URLField, if it's blank, it's fine.
        if not self.instance.image_url:  # Or some other logic if image_url can be complex
            self.fields['image_url'].required = False
