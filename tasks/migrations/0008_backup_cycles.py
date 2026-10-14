from django.db import migrations, models


def preserve_previous_confirmations(apps, schema_editor):
    # The previous confirmation required both the data ZIP and all photos.
    Reminder = apps.get_model("tasks", "BackupReminder")
    Reminder.objects.using(schema_editor.connection.alias).filter(last_confirmed_at__isnull=False).update(
        last_new_photos_confirmed_at=models.F("last_confirmed_at"),
        last_full_photos_confirmed_at=models.F("last_confirmed_at"),
        photos_covered_until=models.F("last_confirmed_at"),
    )


class Migration(migrations.Migration):
    dependencies = [("tasks", "0007_backupreminder")]
    operations = [
        migrations.AddField(model_name="backupreminder", name="last_new_photos_confirmed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="backupreminder", name="last_full_photos_confirmed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="backupreminder", name="photos_covered_until", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="backupreminder", name="popup_seen", field=models.JSONField(blank=True, default=dict)),
        migrations.RunPython(preserve_previous_confirmations, migrations.RunPython.noop),
    ]
