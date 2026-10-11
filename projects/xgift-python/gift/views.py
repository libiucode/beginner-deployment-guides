"""Public status API and permission-controlled operations; never return payment metadata."""
import csv
import re
import secrets
from functools import wraps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponse, HttpResponseForbidden
from django.shortcuts import render, redirect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from gift.forms import RedeemForm, GenerateForm
from gift.models import Order, Code, Folder, Plan, AuditEvent
from gift.services.codes import digest, generate_codes
from gift.services.orders import redeem
from gift.services.vault import unseal
from gift.integrations.live import configured_mode


def mode():
    return configured_mode()


def limited(request, scope, maximum):
    key = 'rate:' + scope + ':' + digest(request.META.get('REMOTE_ADDR', 'unknown'))
    cache.add(key, 0, 60)
    return cache.incr(key) > maximum


def operator_permission(permission):
    def decorate(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path(), '/admin/login/')
            if not request.user.is_active or not request.user.is_staff or not request.user.has_perm(permission):
                return HttpResponseForbidden('没有此操作的权限')
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


@require_GET
def home(request):
    return render(request, 'gift/home.html', {'mode': mode()})


@sensitive_post_parameters('code')
@require_POST
def redeem_api(request):
    if limited(request, 'redeem', 10):
        return JsonResponse({'errors': {'code': ['操作过于频繁，请一分钟后再试。']}}, status=429)
    form = RedeemForm(request.POST)
    if not form.is_valid():
        return JsonResponse({'errors': form.errors}, status=400)
    try:
        with transaction.atomic():
            order = redeem(**form.cleaned_data)
            token = order.access_token
            if token is None:
                token = secrets.token_urlsafe(32)
                Order.objects.filter(pk=order.pk).update(token_digest=digest(token))
    except ValueError as exc:
        return JsonResponse({'errors': {'code': [str(exc)]}}, status=400)
    response = JsonResponse({'id': str(order.pk), 'access_token': token}, status=200)
    response['Cache-Control'] = 'no-store'
    return response


@require_GET
def order_api(request, order_id):
    if limited(request, 'status', 120):
        return JsonResponse({'error': '查询过于频繁'}, status=429)
    order = Order.objects.filter(pk=order_id).first()
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    expected = order.token_digest if order else '0' * 64
    authorized = secrets.compare_digest(expected, digest(token))
    if not authorized or not order:
        return JsonResponse({'error': '无权查询此订单'}, status=403)
    response = JsonResponse({'id': str(order.pk), 'username': order.username, 'status': order.status,
        'status_label': order.get_status_display(), 'plan': order.plan_snapshot.get('name', ''),
        'updated_at': order.updated_at.isoformat(), 'mode': order.mode})
    response['Cache-Control'] = 'no-store'
    return response


@sensitive_post_parameters('code')
@operator_permission('gift.view_order')
@require_http_methods(['GET', 'POST'])
def operator(request):
    if request.method == 'POST' and request.POST.get('action') != 'lookup':
        action = request.POST.get('action')
        permissions = {'folder': 'gift.add_folder', 'generate': 'gift.add_code', 'move': 'gift.change_code', 'revoke': 'gift.change_code'}
        if action not in permissions or not request.user.has_perm(permissions[action]):
            return HttpResponseForbidden('没有此操作的权限')
        try:
            with transaction.atomic():
                if action == 'folder':
                    name = request.POST.get('name', '').strip()
                    if not name or len(name) > 120:
                        raise ValueError('请输入 1 到 120 字的批次名称')
                    folder = Folder.objects.create(name=name)
                    AuditEvent.objects.create(kind='folder_created', details={'folder_id': folder.pk, 'actor_id': request.user.pk})
                elif action == 'generate':
                    form = GenerateForm(request.POST)
                    if not form.is_valid():
                        raise ValueError('请选择启用的套餐、有效批次和 1 到 1000 的数量')
                    generate_codes(form.cleaned_data['plan'].pk, form.cleaned_data['count'], form.cleaned_data['folder'].pk if form.cleaned_data['folder'] else None, actor_id=request.user.pk)
                else:
                    ids = request.POST.getlist('codes')
                    if not ids or len(ids) > 1000 or any(not value.isdigit() for value in ids):
                        raise ValueError('请选择兑换码（最多 1000 个）')
                    rows = Code.objects.filter(pk__in=ids)
                    if action == 'move':
                        folder_id = request.POST.get('folder')
                        folder = Folder.objects.get(pk=folder_id) if folder_id else None
                        count = rows.update(folder=folder)
                    else:
                        count = rows.update(enabled=False)
                    AuditEvent.objects.create(kind='codes_' + action, details={'actor_id': request.user.pk, 'code_ids': list(map(int, ids)), 'count': count})
            messages.success(request, '操作已完成。')
        except (ValueError, Folder.DoesNotExist):
            messages.error(request, '操作未完成，请检查套餐、批次、数量与选择项。')
        return redirect('operator')
    query = request.GET.get('q', '').strip()[:128]
    if query and not re.fullmatch(r'[@A-Za-z0-9_-]{1,36}', query):
        query = ''
    lookup = request.POST.get('code', '').strip() if request.method == 'POST' else ''
    orders = Order.objects.select_related('code').order_by('-created_at')
    codes = Code.objects.filter(order__link_ticket__isnull=True, order__replacement_item__isnull=True).select_related('plan', 'folder').order_by('-created_at')
    if query:
        orders = orders.filter(Q(username__icontains=query) | Q(id__icontains=query))
        codes = codes.filter(order__username__icontains=query)
    if lookup:
        codes = codes.filter(digest=digest(lookup))
        orders = orders.filter(code__digest=digest(lookup))
    folder_id = request.GET.get('folder', '')
    if folder_id.isdigit():
        codes = codes.filter(folder_id=folder_id)
    response = render(request, 'gift/operator.html', {'mode':mode(), 'codes':codes[:200], 'orders':orders[:100],
        'folders':Folder.objects.all(), 'plans':Plan.objects.filter(enabled=True), 'query':query,
        'total_codes':Code.objects.count(), 'total_orders':Order.objects.count(),
        'successes':Order.objects.filter(status=Order.Status.SUCCEEDED, mode=mode()).count(),
        'stats':[{'status':dict(Order.Status.choices).get(row['status'], row['status']), 'count':row['count']} for row in Order.objects.filter(mode=mode()).values('status').annotate(count=Count('id'))], 'folder_id':folder_id})
    response['Cache-Control'] = 'no-store'
    return response


@operator_permission('gift.export_code')
@require_POST
def export_codes(request):
    rows = Code.objects.filter(order__link_ticket__isnull=True, order__replacement_item__isnull=True).select_related('plan', 'folder').order_by('pk')
    folder_id = request.POST.get('folder', '')
    if folder_id:
        if not folder_id.isdigit():
            return HttpResponse('批次无效', status=400)
        rows = rows.filter(folder_id=folder_id)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="xgift-codes.csv"'
    response['Cache-Control'] = 'no-store'
    response.write('\ufeff')
    writer = csv.writer(response)
    writer.writerow(['兑换码', '套餐', '批次', '启用'])
    def safe(value):
        value = str(value)
        return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r')) else value
    count = 0
    for row in rows.iterator():
        writer.writerow([unseal(bytes(row.ciphertext), 'redeem-code').decode(), safe(row.plan.name), safe(row.folder.name if row.folder else ''), row.enabled])
        count += 1
    AuditEvent.objects.create(kind='codes_exported', details={'actor_id':request.user.pk, 'count':count, 'folder_id':folder_id})
    return response
