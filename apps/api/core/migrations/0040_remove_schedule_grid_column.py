from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0039_reschedule_process_route")]

    operations = [
        migrations.DeleteModel(name="ScheduleGridColumn"),
    ]
