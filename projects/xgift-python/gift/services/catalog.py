"""套餐配置验证与冻结。"""

def snapshot(plan):
    if not plan.enabled:
        raise ValueError('套餐已停用')
    if plan.months <= 0 or plan.amount_minor <= 0 or len(plan.currency) != 3 or not plan.currency.isascii() or not plan.currency.isalpha() or not plan.merchant.strip() or not plan.product_id.strip():
        raise ValueError('套餐配置不完整或无效')
    return {'plan_id': plan.pk, 'name': plan.name, 'months': plan.months,
            'amount_minor': plan.amount_minor, 'currency': plan.currency.upper(),
            'merchant': plan.merchant, 'product_id': plan.product_id}

def validate_live_plan(plan):
    import re
    if (type(plan.months) is not int or plan.months <= 0 or type(plan.amount_minor) is not int or plan.amount_minor <= 0
            or not re.fullmatch(r'[A-Z]{3}', plan.currency)
            or not re.fullmatch(r'acct_[A-Za-z0-9]+', plan.merchant)
            or not re.fullmatch(r'prod_[A-Za-z0-9]+', plan.product_id)):
        raise ValueError('live plan configuration is invalid')
