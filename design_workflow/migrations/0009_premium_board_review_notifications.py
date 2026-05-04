from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("design_workflow", "0008_task_work_started_at"),
    ]

    operations = [
        migrations.CreateModel(
            name="SavedView",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120)),
                ("visibility", models.CharField(choices=[("private", "Private"), ("team", "Team")], db_index=True, default="private", max_length=16)),
                ("filters", models.JSONField(blank=True, default=dict)),
                ("sort", models.JSONField(blank=True, default=dict)),
                ("density", models.CharField(choices=[("comfortable", "Comfortable"), ("compact", "Compact")], default="comfortable", max_length=16)),
                ("collapsed_lanes", models.JSONField(blank=True, default=list)),
                ("show_archived", models.BooleanField(default=False)),
                ("is_default", models.BooleanField(db_index=True, default=False)),
                ("owner", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="design_saved_views", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-is_default", "name"),
            },
        ),
        migrations.AddField(
            model_name="task",
            name="review_approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="task",
            name="review_approved_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_design_task_reviews", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="task",
            name="review_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="task",
            name="review_requested_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="requested_design_task_reviews", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="task",
            name="review_state",
            field=models.CharField(choices=[("not_submitted", "Not submitted"), ("needs_review", "Needs review"), ("changes_requested", "Changes requested"), ("approved", "Approved")], db_index=True, default="not_submitted", max_length=24),
        ),
        migrations.AddField(
            model_name="notification",
            name="action_taken_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="notification",
            name="action_taken_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="acted_design_notifications", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="notification",
            name="snoozed_until",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name="NotificationPreference",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("mentions", models.BooleanField(default=True)),
                ("assignments", models.BooleanField(default=True)),
                ("review_requests", models.BooleanField(default=True)),
                ("due_soon", models.BooleanField(default=True)),
                ("digest_frequency", models.CharField(choices=[("instant", "Instant"), ("daily", "Daily"), ("weekly", "Weekly"), ("off", "Off")], default="instant", max_length=16)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="TaskArtifactVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("version_number", models.PositiveIntegerField()),
                ("notes", models.TextField(blank=True)),
                ("approval_state", models.CharField(choices=[("pending", "Pending"), ("changes_requested", "Changes requested"), ("approved", "Approved")], db_index=True, default="pending", max_length=24)),
                ("approved_at", models.DateTimeField(blank=True, null=True)),
                ("approved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="approved_design_artifact_versions", to=settings.AUTH_USER_MODEL)),
                ("attachment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="artifact_versions", to="design_workflow.taskattachment")),
                ("task", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="artifact_versions", to="design_workflow.task")),
                ("uploaded_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="uploaded_design_artifact_versions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ("-version_number", "-created_at"),
            },
        ),
        migrations.CreateModel(
            name="AttachmentAnnotation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("x_percent", models.DecimalField(decimal_places=2, max_digits=5, validators=[MinValueValidator(0), MaxValueValidator(100)])),
                ("y_percent", models.DecimalField(decimal_places=2, max_digits=5, validators=[MinValueValidator(0), MaxValueValidator(100)])),
                ("body", models.TextField()),
                ("resolved", models.BooleanField(db_index=True, default=False)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("attachment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="annotations", to="design_workflow.taskattachment")),
                ("author", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="design_attachment_annotations", to=settings.AUTH_USER_MODEL)),
                ("resolved_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="resolved_design_attachment_annotations", to=settings.AUTH_USER_MODEL)),
                ("version", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="annotations", to="design_workflow.taskartifactversion")),
            ],
            options={
                "ordering": ("created_at",),
            },
        ),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(fields=["review_state", "status"], name="design_work_review__874edf_idx"),
        ),
        migrations.AddIndex(
            model_name="savedview",
            index=models.Index(fields=["owner", "visibility"], name="design_work_owner_i_cd196a_idx"),
        ),
        migrations.AddIndex(
            model_name="savedview",
            index=models.Index(fields=["owner", "is_default"], name="design_work_owner_i_9c228c_idx"),
        ),
        migrations.AddConstraint(
            model_name="savedview",
            constraint=models.UniqueConstraint(fields=("owner", "name"), name="unique_design_saved_view_owner_name"),
        ),
        migrations.AddIndex(
            model_name="taskartifactversion",
            index=models.Index(fields=["task", "approval_state"], name="design_work_task_id_e3f051_idx"),
        ),
        migrations.AddConstraint(
            model_name="taskartifactversion",
            constraint=models.UniqueConstraint(fields=("task", "version_number"), name="unique_design_task_artifact_version"),
        ),
        migrations.AddIndex(
            model_name="attachmentannotation",
            index=models.Index(fields=["attachment", "resolved"], name="design_work_attachm_a5b5aa_idx"),
        ),
        migrations.AlterField(
            model_name="notification",
            name="type",
            field=models.CharField(choices=[("task_assigned", "Task assigned"), ("task_reassigned", "Task reassigned"), ("task_due_soon", "Task due soon"), ("task_overdue", "Task overdue"), ("task_comment", "Task comment"), ("task_status", "Task status"), ("task_blocked", "Task blocked"), ("chat_message", "Chat message"), ("review_requested", "Review requested")], db_index=True, max_length=32),
        ),
        migrations.AlterField(
            model_name="taskactivity",
            name="action_type",
            field=models.CharField(choices=[("created", "Created"), ("updated", "Updated"), ("status_changed", "Status changed"), ("priority_changed", "Priority changed"), ("due_date_changed", "Due date changed"), ("reassigned", "Reassigned"), ("comment_added", "Comment added"), ("time_logged", "Time logged"), ("project_created", "Project created"), ("project_updated", "Project updated"), ("project_archived", "Project archived"), ("label_updated", "Label updated"), ("checklist_updated", "Checklist updated"), ("attachment_added", "Attachment added"), ("task_archived", "Task archived"), ("review_updated", "Review updated"), ("artifact_version_added", "Artifact version added"), ("annotation_added", "Annotation added")], max_length=32),
        ),
    ]
