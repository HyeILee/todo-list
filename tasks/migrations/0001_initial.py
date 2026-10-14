import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=30)),
                ("color_key", models.CharField(choices=[("coral", "산호"), ("leaf", "잎새"), ("ocean", "바다"), ("gold", "금빛"), ("plum", "자두")], default="ocean", max_length=30)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("is_archived", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.CreateModel(
            name="Task",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("task_date", models.DateField(blank=True, db_index=True, null=True)),
                ("title", models.CharField(max_length=200)),
                ("details", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("PENDING", "미정"), ("DONE", "O"), ("NOT_DONE", "X"), ("PARTIAL", "△")], default="PENDING", max_length=10)),
                ("is_important", models.BooleanField(default=False)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tasks", to="tasks.category")),
            ],
            options={"ordering": ["category__sort_order", "category__name", "sort_order", "created_at"]},
        ),
        migrations.CreateModel(
            name="TaskStatusHistory",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("from_status", models.CharField(blank=True, choices=[("PENDING", "미정"), ("DONE", "O"), ("NOT_DONE", "X"), ("PARTIAL", "△")], max_length=10, null=True)),
                ("to_status", models.CharField(choices=[("PENDING", "미정"), ("DONE", "O"), ("NOT_DONE", "X"), ("PARTIAL", "△")], max_length=10)),
                ("changed_at", models.DateTimeField(auto_now_add=True)),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="status_history", to="tasks.task")),
            ],
            options={"ordering": ["-changed_at"]},
        ),
        migrations.AddIndex(model_name="task", index=models.Index(fields=["task_date", "deleted_at", "status"], name="task_date_deleted_status_idx")),
        migrations.AddConstraint(model_name="category", constraint=models.UniqueConstraint(condition=models.Q(("is_archived", False)), fields=("name",), name="unique_active_category_name")),
    ]
