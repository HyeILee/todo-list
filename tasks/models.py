import uuid
from django.conf import settings
from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models.functions import Length

models.CharField.register_lookup(Length)
models.TextField.register_lookup(Length)


class BackupReminder(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    interval_days = models.PositiveSmallIntegerField(default=7, choices=[(7, "매주"), (14, "2주마다"), (30, "30일마다")])
    last_confirmed_at = models.DateTimeField(null=True, blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)
    last_new_photos_confirmed_at = models.DateTimeField(null=True, blank=True)
    last_full_photos_confirmed_at = models.DateTimeField(null=True, blank=True)
    photos_covered_until = models.DateTimeField(null=True, blank=True)
    popup_seen = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(interval_days__in=[7, 14, 30]), name="backup_reminder_valid_interval")]

class Category(models.Model):
    COLORS = [("coral", "산호"), ("leaf", "잎새"), ("ocean", "바다"), ("gold", "금빛"), ("plum", "자두")]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=30)
    color_key = models.CharField(max_length=30, choices=COLORS, default="ocean")
    sort_order = models.PositiveIntegerField(default=0)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name"]
        constraints = [
            models.UniqueConstraint(fields=["name"], condition=models.Q(is_archived=False), name="unique_active_category_name"),
            models.CheckConstraint(condition=models.Q(name__length__lte=30), name="category_name_max_30"),
        ]

    def __str__(self):
        return self.name

class Task(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "미정"
        DONE = "DONE", "O"
        NOT_DONE = "NOT_DONE", "X"
        PARTIAL = "PARTIAL", "△"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_date = models.DateField(null=True, blank=True, db_index=True)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="tasks")
    title = models.CharField(max_length=200)
    details = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    is_important = models.BooleanField(default=False)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["category__sort_order", "category__name", "sort_order", "created_at"]
        indexes = [models.Index(fields=["task_date", "deleted_at", "status"], name="task_date_deleted_status_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(title__length__lte=200), name="task_title_max_200"),
            models.CheckConstraint(condition=models.Q(details__length__lte=2000), name="task_details_max_2000"),
        ]

    def __str__(self):
        return self.title

class TaskStatusHistory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="status_history")
    from_status = models.CharField(max_length=10, choices=Task.Status.choices, null=True, blank=True)
    to_status = models.CharField(max_length=10, choices=Task.Status.choices)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]


class DailyRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record_date = models.DateField(unique=True)
    today_story = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])
    memorable_sentence = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])
    stayed_with_me = models.TextField(blank=True, validators=[MaxLengthValidator(2000)])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-record_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(today_story__gt="") | models.Q(memorable_sentence__gt="") | models.Q(stayed_with_me__gt=""),
                name="daily_record_requires_content",
            ),
            models.CheckConstraint(condition=models.Q(today_story__length__lte=2000), name="record_story_max_2000"),
            models.CheckConstraint(condition=models.Q(memorable_sentence__length__lte=2000), name="record_sentence_max_2000"),
            models.CheckConstraint(condition=models.Q(stayed_with_me__length__lte=2000), name="record_stayed_max_2000"),
        ]

    def __str__(self):
        return str(self.record_date)


class Holiday(models.Model):
    holiday_date = models.DateField(primary_key=True)
    name = models.CharField(max_length=100)
    source = models.CharField(max_length=20, default="KASI")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["holiday_date"]

    def __str__(self):
        return f"{self.holiday_date} {self.name}"


class DailyPhoto(models.Model):
    class Category(models.TextChoices):
        LIFE = "LIFE", "Life"
        WORK = "WORK", "Work"
        ETC = "ETC", "Etc"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    record_date = models.DateField(db_index=True)
    category = models.CharField(max_length=4, choices=Category.choices, default=Category.ETC, db_index=True)
    blob_url = models.URLField(max_length=1000)
    blob_pathname = models.CharField(max_length=1024)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size_bytes = models.PositiveIntegerField()
    position = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-record_date", "position", "created_at"]
        constraints = [
            models.UniqueConstraint(fields=["record_date", "position"], name="unique_daily_photo_position"),
            models.CheckConstraint(condition=models.Q(position__gte=0, position__lte=9), name="daily_photo_position_0_9"),
        ]

    def __str__(self):
        return f"{self.record_date} {self.original_name}"


class BackupLog(models.Model):
    class BackupType(models.TextChoices):
        DAILY = "DAILY", "일별 자동"
        MONTHLY = "MONTHLY", "월별 보관"
        MANUAL = "MANUAL", "수동 전체"
        PRE_RESTORE = "PRE_RESTORE", "복원 전 안전 백업"

    class Status(models.TextChoices):
        STARTED = "STARTED", "진행 중"
        SUCCESS = "SUCCESS", "성공"
        FAILED = "FAILED", "실패"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    backup_type = models.CharField(max_length=12, choices=BackupType.choices)
    file_name = models.CharField(max_length=255, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=7, choices=Status.choices)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]


class LoginSecurity(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    enabled = models.BooleanField(default=False)
    generation = models.UUIDField(default=uuid.uuid4)
    failures = models.PositiveSmallIntegerField(default=0)
    blocked_until = models.DateTimeField(null=True, blank=True)


class Passkey(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    credential_id = models.CharField(max_length=1024, unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveBigIntegerField(default=0)
    label = models.CharField(max_length=60, default="내 패스키")
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)


class TrustedBrowser(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    token_hash = models.CharField(max_length=64, unique=True)
    generation = models.UUIDField()
    auth_hash = models.CharField(max_length=64)
    network_hash = models.CharField(max_length=64)
    browser_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)


class RecoveryCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    generation = models.UUIDField()
    code_hash = models.CharField(max_length=64, unique=True)
    used_at = models.DateTimeField(null=True, blank=True)


class PasskeyChallenge(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    purpose = models.CharField(max_length=10)
    session_hash = models.CharField(max_length=64)
    auth_hash = models.CharField(max_length=64)
    generation = models.UUIDField()
    challenge = models.BinaryField()
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
