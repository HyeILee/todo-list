from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tasks", "0005_holidays_photos_and_record_limits")]

    operations = [
        migrations.AddField(
            model_name="dailyphoto",
            name="category",
            field=models.CharField(
                choices=[("LIFE", "Life"), ("WORK", "Work"), ("ETC", "Etc")],
                db_index=True,
                default="ETC",
                max_length=4,
            ),
        ),
    ]
