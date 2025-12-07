from django import forms

from core.models import Contact


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ['name', 'email', 'subject', 'message']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Enter your name',
                                           'class': 'form-input flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white '
                                                    'focus:outline-0 focus:ring-0 border border-[#432e6b] bg-[#221736] focus:border-[#432e6b] '
                                                    'h-14 placeholder:text-[#a48dce] p-[15px] text-base font-normal leading-normal'}),
            'email': forms.EmailInput(attrs={'placeholder': 'Enter your email',
                                             'class': 'form-input flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white '
                                                      'focus:outline-0 focus:ring-0 border border-[#432e6b] bg-[#221736] focus:border-[#432e6b] '
                                                      'h-14 placeholder:text-[#a48dce] p-[15px] text-base font-normal leading-normal'}),
            'subject': forms.TextInput(attrs={'placeholder': 'Enter your subject',
                                              'class': 'form-input flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white '
                                                       'focus:outline-0 focus:ring-0 border border-[#432e6b] bg-[#221736] focus:border-[#432e6b] '
                                                       'h-14 placeholder:text-[#a48dce] p-[15px] text-base font-normal leading-normal'}),
            'message': forms.Textarea(attrs={'placeholder': 'Enter your message',
                                             'class': 'form-input flex w-full min-w-0 flex-1 resize-none overflow-hidden rounded-lg text-white '
                                                      'focus:outline-0 focus:ring-0 border border-[#432e6b] bg-[#221736] focus:border-[#432e6b] '
                                                      'h-14 placeholder:text-[#a48dce] p-[15px] text-base font-normal leading-normal',
                                             'rows': 4}),
        }
