import uuid

from django.core.validators import MaxLengthValidator
from django.db import migrations, models


def remove_empty_records(apps, schema_editor):
    daily_record = apps.get_model("tasks", "DailyRecord")
    daily_record.objects.using(schema_editor.connection.alias).filter(
        today_story="",
        memorable_sentence="",
        stayed_with_me="",
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("tasks", "0002_dailyrecord")]
    operations = [
        migrations.AlterField(
            model_name="task",
            name="details",
            field=models.TextField(blank=True, validators=[MaxLengthValidator(2000)]),
        ),
        migrations.AlterField(
            model_name="dailyrecord",
            name="today_story",
            field=models.TextField(blank=True, validators=[MaxLengthValidator(500)]),
        ),
        migrations.AlterField(
            model_name="dailyrecord",
            name="memorable_sentence",
            field=models.TextField(blank=True, validators=[MaxLengthValidator(300)]),
        ),
        migrations.AlterField(
            model_name="dailyrecord",
            name="stayed_with_me",
            field=models.TextField(blank=True, validators=[MaxLengthValidator(300)]),
        ),
        migrations.RunPython(remove_empty_records, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="dailyrecord",
            constraint=models.CheckConstraint(
                condition=models.Q(("today_story__gt", "")) | models.Q(("memorable_sentence__gt", "")) | models.Q(("stayed_with_me__gt", "")),
                name="daily_record_requires_content",
            ),
        ),
        migrations.CreateModel(
            name="BackupLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("backup_type", models.CharField(choices=[("DAILY", "일별 자동"), ("MONTHLY", "월별 보관"), ("MANUAL", "수동 전체"), ("PRE_RESTORE", "복원 전 안전 백업")], max_length=12)),
                ("file_name", models.CharField(blank=True, max_length=255)),
                ("sha256", models.CharField(blank=True, max_length=64)),
                ("status", models.CharField(choices=[("STARTED", "진행 중"), ("SUCCESS", "성공"), ("FAILED", "실패")], max_length=7)),
                ("started_at", models.DateTimeField()),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("message", models.TextField(blank=True)),
            ],
            options={"ordering": ["-started_at"]},
        ),
    ]
