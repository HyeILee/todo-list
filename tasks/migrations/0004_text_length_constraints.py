from django.db import migrations, models
from django.db.models.functions import Length


models.CharField.register_lookup(Length)
models.TextField.register_lookup(Length)


class Migration(migrations.Migration):
    dependencies = [("tasks", "0003_backup_log_and_constraints")]
    operations = [
        migrations.AddConstraint("category", models.CheckConstraint(condition=models.Q(name__length__lte=30), name="category_name_max_30")),
        migrations.AddConstraint("task", models.CheckConstraint(condition=models.Q(title__length__lte=200), name="task_title_max_200")),
        migrations.AddConstraint("task", models.CheckConstraint(condition=models.Q(details__length__lte=2000), name="task_details_max_2000")),
        migrations.AddConstraint("dailyrecord", models.CheckConstraint(condition=models.Q(today_story__length__lte=500), name="record_story_max_500")),
        migrations.AddConstraint("dailyrecord", models.CheckConstraint(condition=models.Q(memorable_sentence__length__lte=300), name="record_sentence_max_300")),
        migrations.AddConstraint("dailyrecord", models.CheckConstraint(condition=models.Q(stayed_with_me__length__lte=300), name="record_stayed_max_300")),
    ]
