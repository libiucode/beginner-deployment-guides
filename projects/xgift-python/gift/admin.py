from django import forms
from django.contrib import admin
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from gift.forms_credentials import CREDENTIAL_TYPES, validate_credential
from gift.models import Plan, Folder, Code, Order, AuditEvent, Secret
from gift.services.vault import seal, unseal

admin.site.site_header = 'X · 管理中心'
admin.site.site_title = 'X'
admin.site.index_title = '配置与业务管理'

@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'months', 'amount_minor', 'currency', 'merchant', 'enabled')
    list_filter = ('enabled', 'currency')
    search_fields = ('name', 'merchant')

@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name',)

@admin.register(Code)
class CodeAdmin(admin.ModelAdmin):
    list_display = ('id', 'plan', 'folder', 'enabled', 'created_at')
    list_filter = ('enabled', 'folder', 'plan')
    fields = ('plan', 'folder', 'enabled', 'full_code', 'created_at')
    readonly_fields = ('full_code', 'created_at')
    def get_queryset(self, request):
        return super().get_queryset(request).filter(order__link_ticket__isnull=True, order__replacement_item__isnull=True)
    def has_add_permission(self, request):
        return False
    def get_fields(self, request, obj=None):
        return self.fields if request.user.has_perm('gift.reveal_code') else tuple(f for f in self.fields if f != 'full_code')
    def change_view(self, request, object_id, form_url='', extra_context=None):
        response = super().change_view(request, object_id, form_url, extra_context)
        if hasattr(response, 'context_data') and response.status_code == 200 and request.user.has_perm('gift.reveal_code') and response.context_data.get('original'):
            AuditEvent.objects.create(kind='code_revealed', details={'actor_id':request.user.pk, 'code_id':object_id})
        response['Cache-Control'] = 'no-store'
        return response
    @admin.display(description='完整兑换码（仅授权人员可见）')
    def full_code(self, obj):
        return unseal(bytes(obj.ciphertext), 'redeem-code').decode() if obj else ''

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'username', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('username', 'id')
    exclude = ('token_digest', 'remote_session_id', 'recipient_id', 'payment_url')
    readonly_fields = ('id', 'code', 'username', 'plan_snapshot', 'mode', 'status', 'created_at', 'updated_at')
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(AuditEvent)
class AuditAdmin(admin.ModelAdmin):
    list_display = ('kind', 'order', 'created_at')
    readonly_fields = ('kind', 'order', 'details', 'created_at')
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
    def has_change_permission(self, request, obj=None):
        return False

class SecretForm(forms.ModelForm):
    name = forms.ChoiceField(label="配置类型", choices=CREDENTIAL_TYPES)
    value = forms.CharField(label='新配置值', widget=forms.PasswordInput(render_value=False), required=False, help_text='留空保留原值；保存后不再显示。')
    class Meta:
        model = Secret
        fields = ('name', 'value')
    def clean(self):
        data = super().clean()
        name = self.instance.name if self.instance.pk else data.get('name')
        if not self.instance.pk and not data.get('value'):
            self.add_error('value', '请输入配置值')
        if data.get('value') and name:
            try:
                data['value'] = validate_credential(name, data['value'])
            except forms.ValidationError as exc:
                self.add_error('value', exc)
        return data

@admin.register(Secret)
class SecretAdmin(admin.ModelAdmin):
    form = SecretForm
    fields = ('name', 'value')
    list_display = ('name',)
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser
    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)
    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission
    @method_decorator(sensitive_post_parameters('value'))
    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        response = super().changeform_view(request, object_id, form_url, extra_context)
        response['Cache-Control'] = 'no-store'
        return response
    def get_readonly_fields(self, request, obj=None):
        return ('name',) if obj else ()
    def save_model(self, request, obj, form, change):
        if form.cleaned_data.get('value'):
            obj.ciphertext = seal(form.cleaned_data['value'].encode(), 'secret:' + obj.name)
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(kind='secret_updated', details={'actor_id':request.user.pk, 'secret_id':obj.pk})
