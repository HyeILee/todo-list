from datetime import date
from django.urls import path, register_converter
from . import views, import_views


class DateConverter:
    regex = r"[0-9]{4}-[0-9]{2}-[0-9]{2}"

    def to_python(self, value):
        return date.fromisoformat(value)

    def to_url(self, value):
        return value.isoformat() if isinstance(value, date) else date.fromisoformat(value).isoformat()


register_converter(DateConverter, "date")

urlpatterns = [path("tasks/import/", import_views.task_import, name="task_import"), path("tasks/import/template.xlsx", import_views.task_import_template, name="task_import_template"), path("", views.dashboard, name="home"), path("day/<date:selected_date>/", views.dashboard, name="day"), path("tasks/new/", views.task_create, name="task_create"), path("tasks/all/", views.task_list, name="task_list"), path("tasks/reorder/", views.task_reorder, name="task_reorder"), path("tasks/<uuid:task_id>/edit/", views.task_edit, name="task_edit"), path("tasks/<uuid:task_id>/retry/", views.task_retry, name="task_retry"), path("tasks/<uuid:task_id>/status/", views.task_status, name="task_status"), path("tasks/<uuid:task_id>/delete/", views.task_delete, name="task_delete"), path("tasks/<uuid:task_id>/restore/", views.task_restore, name="task_restore"), path("tasks/<uuid:task_id>/move/", views.task_move, name="task_move"), path("records/", views.record_list, name="record_list"), path("records/<date:record_date>/save/", views.record_save, name="record_save"), path("photos/", views.photo_album, name="photo_album"), path("photos/manifest/", views.photo_manifest, name="photo_manifest"), path("photos/<uuid:photo_id>/delete/", views.photo_delete, name="photo_delete"), path("photos/<uuid:photo_id>/content/", views.photo_content, name="photo_content"), path("photos/<date:record_date>/upload/", views.photo_upload, name="photo_upload"), path("settings/", views.settings_home, name="settings_home"), path("settings/trash/", views.task_trash, name="task_trash"), path("settings/backups/", views.backup_page, name="backup_page"), path("settings/backups/reminder/", views.backup_reminder_update, name="backup_reminder_update"), path("settings/backups/create/", views.create_backup, name="create_backup"), path("settings/backups/restore/", views.restore_backup, name="restore_backup"), path("inbox/", views.inbox, name="inbox"), path("categories/", views.categories, name="categories"), path("categories/<uuid:category_id>/archive/", views.category_archive, name="category_archive"), path("categories/<uuid:category_id>/move/", views.category_move, name="category_move"), path("exports/tasks.<str:extension>", views.export_tasks, name="export_tasks"), path("exports/records.<str:extension>", views.export_records, name="export_records")]
