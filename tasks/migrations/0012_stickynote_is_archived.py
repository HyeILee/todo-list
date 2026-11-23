from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tasks", "0011_stickynote")]
    operations = [migrations.AddField(model_name="stickynote", name="is_archived", field=models.BooleanField(default=False))]
