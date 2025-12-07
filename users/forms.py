from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm

from users.models import CustomUser


class SignUpForm(UserCreationForm):
    profile_picture = forms.ImageField(required=False)

    class Meta:
        model = CustomUser
        fields = ('profile_picture', 'username',
                  'email', 'password1', 'password2')


class LoginForm(AuthenticationForm):
    class Meta:
        model = CustomUser


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ('profile_picture', 'bio', 'location', 'birth_date')


class UserPreferencesForm(forms.Form):
    language = forms.ChoiceField(choices=[
        ('en', 'English'),
        ('es', 'Spanish'),
        ('fr', 'French'),
        ('de', 'German'),
        ('it', 'Italian'),
        ('pt', 'Portuguese'),
        ('ru', 'Russian'),
        ('zh-hans', 'Simplified Chinese'),
        ('ja', 'Japanese'),
        ('ko', 'Korean'),
        ('hi', 'Hindi'),
        ('ar', 'Arabic'),
        ('nl', 'Dutch'),
        ('tr', 'Turkish'),
        ('vi', 'Vietnamese'),
        ('pl', 'Polish'),
    ])
