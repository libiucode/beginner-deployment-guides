from uuid import UUID
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from gift.models import RecoveryBatch, RecoveryPreview, Order
from gift.views import operator_permission, mode
from gift.services.recovery import preview_recovery, start_recovery, stop_recovery

@operator_permission('gift.change_order')
@require_http_methods(['GET', 'POST'])
def recovery_page(request):
    preview = None
    if request.method == 'POST':
        try:
            action = request.POST.get('action')
            if action == 'preview':
                ids = [UUID(value.strip()) for value in request.POST.get('orders', '').splitlines() if value.strip()]
                preview = preview_recovery(ids, request.POST.get('mode'), unpaid_confirmed=request.POST.get('unpaid') == 'yes')
            elif action == 'start':
                start_recovery(UUID(request.POST['preview']), request.user.pk)
                messages.success(request, '补单已排队；网页不会直接付款。')
                return redirect('recovery')
            elif action == 'stop':
                stop_recovery(UUID(request.POST['batch']))
                messages.success(request, '已停止后续任务；当前订单会完成核实。')
                return redirect('recovery')
            else: raise ValueError('无效操作')
        except (ValueError, KeyError, RecoveryBatch.DoesNotExist, RecoveryPreview.DoesNotExist, Order.DoesNotExist) as exc:
            messages.error(request, str(exc) if isinstance(exc, ValueError) else '记录不存在')
    response = render(request, 'gift/recovery.html', {'mode': mode(), 'preview': preview,
        'batches': RecoveryBatch.objects.select_related('preview').prefetch_related('items__order', 'items__replacement').order_by('-created_at')[:50]})
    response['Cache-Control'] = 'no-store'
    return response
