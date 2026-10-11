from django.urls import path
from gift import views
from gift import link_views, recovery_views
urlpatterns = [path('', views.home, name='home'), path('api/redeem', views.redeem_api), path('api/orders/<uuid:order_id>', views.order_api), path('operator/', views.operator, name='operator'), path('operator/export', views.export_codes, name='export_codes')]
urlpatterns += [path('links/', link_views.link_page, name='links'), path('api/links', link_views.link_request),
    path('api/links/<uuid:order_id>', link_views.link_status), path('api/links/<uuid:order_id>/cancel', link_views.link_cancel),
    path('api/links/<uuid:order_id>/reconcile', link_views.link_reconcile),
    path('operator/recovery/', recovery_views.recovery_page, name='recovery')]
