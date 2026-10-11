from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST
from gift.models import Plan
from gift.services.links import request_link, authorized_link, cancel_link, request_link_reconciliation
from gift.views import limited, mode

def token(request):
    return request.headers.get('Authorization', '').removeprefix('Bearer ')

@require_GET
def link_page(request):
    return render(request, 'gift/links.html', {'mode': mode(), 'plans': Plan.objects.filter(enabled=True)})

@require_POST
def link_request(request):
    if limited(request, 'link-request', 10): return JsonResponse({'error': '操作过于频繁'}, status=429)
    try:
        order = request_link(int(request.POST.get('plan', '')), request.POST.get('username', ''))
    except (ValueError, TypeError): return JsonResponse({'error': '请选择有效套餐及用户名'}, status=400)
    response = JsonResponse({'id': str(order.pk), 'access_token': order.access_token})
    response['Cache-Control'] = 'no-store'
    return response

@require_GET
def link_status(request, order_id):
    if limited(request, 'link-status', 120): return JsonResponse({'error': '查询过于频繁'}, status=429)
    try: order = authorized_link(order_id, token(request))
    except PermissionError: return JsonResponse({'error': '无权查询此队列票据'}, status=403)
    response = JsonResponse({'id': str(order.pk), 'status': order.status, 'status_label': order.get_status_display(),
        'payment_url': order.payment_url, 'mode': order.mode})
    response['Cache-Control'] = 'no-store'
    return response

@require_POST
def link_cancel(request, order_id):
    try: cancel_link(order_id, token(request))
    except PermissionError: return JsonResponse({'error': '无权操作此队列票据'}, status=403)
    except ValueError as exc: return JsonResponse({'error': str(exc)}, status=409)
    response = JsonResponse({'status': 'cancelled'})
    response['Cache-Control'] = 'no-store'
    return response

@require_POST
def link_reconcile(request, order_id):
    if limited(request, 'link-reconcile', 10): return JsonResponse({'error': '操作过于频繁'}, status=429)
    try: request_link_reconciliation(order_id, token(request))
    except PermissionError: return JsonResponse({'error': '无权操作此队列票据'}, status=403)
    except ValueError as exc: return JsonResponse({'error': str(exc)}, status=409)
    response = JsonResponse({'message': '已排队核实原会话；不会再次付款'})
    response['Cache-Control'] = 'no-store'
    return response
