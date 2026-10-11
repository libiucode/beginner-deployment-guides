from django import forms
from gift.models import Plan, Folder
from gift.services.orders import normalize_username

class RedeemForm(forms.Form):
    code = forms.CharField(max_length=128, label='兑换码')
    username = forms.CharField(max_length=16, label='X 用户名')
    def clean_username(self):
        try:
            return normalize_username(self.cleaned_data['username'])
        except ValueError as exc:
            raise forms.ValidationError(str(exc)) from None

class GenerateForm(forms.Form):
    plan = forms.ModelChoiceField(queryset=Plan.objects.filter(enabled=True), label='套餐')
    count = forms.IntegerField(min_value=1, max_value=1000, label='数量')
    folder = forms.ModelChoiceField(queryset=Folder.objects.all(), required=False, label='批次')
