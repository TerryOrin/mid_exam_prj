from django import forms


class ContactForm(forms.Form):
    name = forms.CharField(
        label="姓名",
        max_length=100,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "請輸入姓名"}
        ),
    )
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(
            attrs={"class": "form-control", "placeholder": "請輸入 Email"}
        ),
    )
    subject = forms.CharField(
        label="主旨",
        max_length=200,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "請輸入主旨"}
        ),
    )
    message = forms.CharField(
        label="訊息內容",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 6,
                "placeholder": "請輸入想要聯繫的內容…",
            }
        ),
    )
