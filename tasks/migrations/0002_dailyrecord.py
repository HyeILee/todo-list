import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tasks", "0001_initial")]
    operations = [
        migrations.CreateModel(
            name="DailyRecord",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("record_date", models.DateField(unique=True)),
                ("today_story", models.TextField(blank=True)),
                ("memorable_sentence", models.TextField(blank=True)),
                ("stayed_with_me", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-record_date"]},
        ),
    ]
