from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('gift', '0002_folder_code_order_auditevent_plan_code_plan')]
    operations = [migrations.RunSQL(
        """CREATE TRIGGER gift_order_frozen BEFORE UPDATE ON gift_order
        WHEN NEW.plan_snapshot IS NOT OLD.plan_snapshot
          OR NEW.username IS NOT OLD.username
          OR NEW.code_id IS NOT OLD.code_id
        BEGIN SELECT RAISE(ABORT, '订单绑定和套餐快照不可修改'); END;""",
        'DROP TRIGGER gift_order_frozen;',
    )]
