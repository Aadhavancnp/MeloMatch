from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from bootstrap_datepicker_plus.widgets import DatePickerInput
from users.models import CustomUser


class SignUpForm(UserCreationForm):
    profile_picture = forms.ImageField(required=False)

    class Meta:
        model = CustomUser
        fields = ('profile_picture', 'username', 'email', 'password1', 'password2')
        widgets = {
            'date_of_birth': DatePickerInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for myField in self.fields:
            self.fields.get(myField).widget.attrs['class'] = 'form-control'


class LoginForm(AuthenticationForm):
    class Meta:
        model = CustomUser

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for myField in self.fields:
            self.fields.get(myField).widget.attrs['class'] = 'form-control'


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = CustomUser
        fields = ('profile_picture', 'bio', 'location', 'birth_date')
        widgets = {
            'birth_date': DatePickerInput(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for myField in self.fields:
            self.fields.get(myField).widget.attrs['class'] = 'form-control'


class UserPreferencesForm(forms.Form):
    language = forms.ChoiceField(choices=[('en', 'English'), ('es', 'Spanish'), ('fr', 'French')])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for myField in self.fields:
            self.fields.get(myField).widget.attrs['class'] = 'form-control'
