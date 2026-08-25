from django.db import migrations, models
from django.db.models import Count, F, Q


def audit_legacy_vote_evidence(apps, schema_editor):
    del schema_editor
    RescheduleRequest = apps.get_model("core", "RescheduleRequest")
    TeamConfirmation = apps.get_model("core", "TeamConfirmation")

    invalid_purposes = TeamConfirmation.objects.exclude(
        purpose__in=["OPPONENT", "VOTER"]
    )
    if invalid_purposes.exists():
        values = list(invalid_purposes.values_list("purpose", flat=True).distinct()[:10])
        raise RuntimeError(f"invalid TeamConfirmation purpose values: {values}")

    invalid_responses = TeamConfirmation.objects.exclude(
        response__in=["PENDING", "ACCEPTED", "REJECTED"]
    )
    if invalid_responses.exists():
        values = list(invalid_responses.values_list("response", flat=True).distinct()[:10])
        raise RuntimeError(f"invalid TeamConfirmation response values: {values}")

    invalid_response_evidence = TeamConfirmation.objects.exclude(
        Q(response="PENDING", responded_by_id__isnull=True, responded_at__isnull=True)
        | Q(
            response__in=["ACCEPTED", "REJECTED"],
            responded_by_id__isnull=False,
            responded_at__isnull=False,
        )
    )
    if invalid_response_evidence.exists():
        values = list(invalid_response_evidence.values_list("id", flat=True)[:10])
        raise RuntimeError(
            "invalid TeamConfirmation response evidence: "
            f"response/actor/time are inconsistent: {values}"
        )

    same_week_voter_ids = TeamConfirmation.objects.filter(
        purpose="VOTER",
        request__request_type="SAME_WEEK",
    ).values_list("request_id", flat=True)
    if same_week_voter_ids.exists():
        values = list(same_week_voter_ids.distinct()[:10])
        raise RuntimeError(
            "invalid legacy reschedule vote evidence: "
            f"same-week requests have VOTER confirmations: {values}"
        )

    requests = RescheduleRequest.objects.annotate(
        voter_total=Count(
            "confirmations",
            filter=Q(confirmations__purpose="VOTER"),
        ),
        voter_pending=Count(
            "confirmations",
            filter=Q(
                confirmations__purpose="VOTER",
                confirmations__response="PENDING",
            ),
        ),
        voter_accepted=Count(
            "confirmations",
            filter=Q(
                confirmations__purpose="VOTER",
                confirmations__response="ACCEPTED",
            ),
        ),
        voter_rejected=Count(
            "confirmations",
            filter=Q(
                confirmations__purpose="VOTER",
                confirmations__response="REJECTED",
            ),
        ),
    )
    invalid_selected = requests.filter(status="WAITING_SELECTED_TEAMS").exclude(
        request_type="CROSS_WEEK",
        voter_total__gte=1,
        voter_pending__gte=1,
        voter_rejected=0,
    )
    if invalid_selected.exists():
        values = list(invalid_selected.values_list("id", flat=True)[:10])
        raise RuntimeError(
            "invalid legacy reschedule vote evidence: "
            f"WAITING_SELECTED_TEAMS requests are inconsistent: {values}"
        )

    invalid_final = requests.filter(status="WAITING_ADMIN_FINAL").exclude(
        request_type="CROSS_WEEK",
        voter_total__gte=1,
        voter_total=F("voter_accepted"),
    )
    if invalid_final.exists():
        values = list(invalid_final.values_list("id", flat=True)[:10])
        raise RuntimeError(
            "invalid legacy reschedule vote evidence: "
            f"WAITING_ADMIN_FINAL requests are inconsistent: {values}"
        )


def backfill_process_route(apps, schema_editor):
    del schema_editor
    RescheduleRequest = apps.get_model("core", "RescheduleRequest")
    TeamConfirmation = apps.get_model("core", "TeamConfirmation")
    AdminAuditLog = apps.get_model("core", "AdminAuditLog")
    RescheduleRequest.objects.filter(request_type="SAME_WEEK").update(
        process_route="ORDINARY"
    )
    RescheduleRequest.objects.filter(request_type="CROSS_WEEK").update(
        process_route="HANDBOOK_REVIEW"
    )
    RescheduleRequest.objects.filter(
        request_type="CROSS_WEEK",
        status__in=["WAITING_SELECTED_TEAMS", "WAITING_ADMIN_FINAL", "APPROVED"],
    ).update(review_classification="CROSS_ROUND")
    voter_evidence_ids = TeamConfirmation.objects.filter(purpose="VOTER").values_list(
        "request_id", flat=True
    )
    audit_evidence_ids = AdminAuditLog.objects.filter(
        object_type="RescheduleRequest",
        action__in=[
            "reschedule.admin_reject",
            "reschedule.admin_vote",
            "reschedule.admin_final_approve",
            "reschedule.admin_final_reject",
        ],
    ).values_list("object_id", flat=True)
    RescheduleRequest.objects.filter(
        request_type="CROSS_WEEK",
        id__in=voter_evidence_ids,
    ).update(review_classification="CROSS_ROUND")
    RescheduleRequest.objects.filter(
        request_type="CROSS_WEEK",
        id__in=audit_evidence_ids,
    ).update(review_classification="CROSS_ROUND")


def audit_reschedule_value_domains(apps, schema_editor):
    del schema_editor
    RescheduleRequest = apps.get_model("core", "RescheduleRequest")
    checks = {
        "request_type": ["SAME_WEEK", "CROSS_WEEK"],
        "process_route": [None, "ORDINARY", "HANDBOOK_REVIEW"],
        "review_classification": [None, "ORDINARY", "CROSS_ROUND"],
        "status": [
            "WAITING_OPPONENT",
            "WAITING_ADMIN_DECISION",
            "WAITING_SELECTED_TEAMS",
            "WAITING_ADMIN_FINAL",
            "APPROVED",
            "REJECTED",
            "WITHDRAWN",
            "EXPIRED",
            "ADMIN_CANCELLED",
        ],
    }
    for field, allowed in checks.items():
        allowed_values = [value for value in allowed if value is not None]
        allowed_query = Q(**{f"{field}__in": allowed_values})
        if None in allowed:
            allowed_query |= Q(**{f"{field}__isnull": True})
        invalid = RescheduleRequest.objects.exclude(allowed_query)
        if invalid.exists():
            values = list(invalid.values_list(field, flat=True).distinct()[:10])
            raise RuntimeError(f"invalid RescheduleRequest {field} values: {values}")


class Migration(migrations.Migration):
    atomic = True

    dependencies = [("core", "0038_validate_season_scope_foreign_keys")]

    operations = [
        migrations.AlterField(
            model_name="reschedulerequest",
            name="request_type",
            field=models.CharField(
                choices=[
                    ("SAME_WEEK", "同一自然周"),
                    ("CROSS_WEEK", "跨自然周"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="reschedulerequest",
            name="process_route",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ORDINARY", "普通流程"),
                    ("HANDBOOK_REVIEW", "参赛手册审核"),
                ],
                max_length=24,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="reschedulerequest",
            name="review_classification",
            field=models.CharField(
                blank=True,
                choices=[
                    ("ORDINARY", "按普通办法"),
                    ("CROSS_ROUND", "跨轮次调整"),
                ],
                max_length=16,
                null=True,
            ),
        ),
        migrations.RunPython(audit_legacy_vote_evidence, migrations.RunPython.noop),
        migrations.RunPython(backfill_process_route, migrations.RunPython.noop),
        migrations.RunPython(audit_reschedule_value_domains, migrations.RunPython.noop),
        migrations.RunSQL(
            sql="SET CONSTRAINTS ALL IMMEDIATE",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=Q(request_type__in=["SAME_WEEK", "CROSS_WEEK"]),
                name="reschedule_request_type_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    Q(process_route__isnull=True)
                    | Q(process_route__in=["ORDINARY", "HANDBOOK_REVIEW"])
                ),
                name="reschedule_process_route_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    Q(review_classification__isnull=True)
                    | Q(review_classification__in=["ORDINARY", "CROSS_ROUND"])
                ),
                name="reschedule_review_classification_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=Q(
                    status__in=[
                        "WAITING_OPPONENT",
                        "WAITING_ADMIN_DECISION",
                        "WAITING_SELECTED_TEAMS",
                        "WAITING_ADMIN_FINAL",
                        "APPROVED",
                        "REJECTED",
                        "WITHDRAWN",
                        "EXPIRED",
                        "ADMIN_CANCELLED",
                    ]
                ),
                name="reschedule_status_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    Q(process_route__isnull=True)
                    | ~Q(request_type="CROSS_WEEK")
                    | Q(process_route="HANDBOOK_REVIEW")
                ),
                name="reschedule_cross_week_requires_review_route",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    ~Q(review_classification="ORDINARY")
                    | Q(request_type="SAME_WEEK")
                ),
                name="reschedule_ordinary_classification_same_week",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    ~Q(review_classification="ORDINARY")
                    | Q(status="APPROVED")
                ),
                name="reschedule_ordinary_classification_approved",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    Q(review_classification__isnull=True)
                    | (
                        Q(process_route__isnull=False)
                        & Q(process_route="HANDBOOK_REVIEW")
                    )
                ),
                name="reschedule_classification_requires_review_route",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    Q(review_classification__isnull=True)
                    | ~Q(
                        status__in=[
                            "WAITING_OPPONENT",
                            "WAITING_ADMIN_DECISION",
                        ]
                    )
                ),
                name="reschedule_classification_after_admin_decision",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    ~Q(
                        status__in=[
                            "WAITING_SELECTED_TEAMS",
                            "WAITING_ADMIN_FINAL",
                        ]
                    )
                    | (
                        Q(process_route="HANDBOOK_REVIEW")
                        & Q(review_classification__isnull=False)
                        & Q(review_classification="CROSS_ROUND")
                    )
                    | (
                        Q(process_route__isnull=True)
                        & Q(request_type="CROSS_WEEK")
                        & Q(review_classification__isnull=True)
                    )
                ),
                name="reschedule_vote_states_are_cross_round",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    Q(process_route__isnull=True)
                    | ~Q(process_route="ORDINARY")
                    | (
                        Q(request_type="SAME_WEEK")
                        & ~Q(
                            status__in=[
                                "WAITING_ADMIN_DECISION",
                                "WAITING_SELECTED_TEAMS",
                                "WAITING_ADMIN_FINAL",
                            ]
                        )
                    )
                ),
                name="reschedule_ordinary_route_stays_ordinary",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    Q(process_route__isnull=False)
                    | ~Q(request_type="SAME_WEEK")
                    | ~Q(
                        status__in=[
                            "WAITING_ADMIN_DECISION",
                            "WAITING_SELECTED_TEAMS",
                            "WAITING_ADMIN_FINAL",
                        ]
                    )
                ),
                name="reschedule_legacy_same_week_no_review_states",
            ),
        ),
        migrations.AddConstraint(
            model_name="reschedulerequest",
            constraint=models.CheckConstraint(
                condition=(
                    ~Q(process_route="HANDBOOK_REVIEW", status="APPROVED")
                    | (
                        Q(review_classification__isnull=False)
                        & Q(
                            review_classification__in=[
                                "ORDINARY",
                                "CROSS_ROUND",
                            ]
                        )
                    )
                ),
                name="reschedule_handbook_approval_classified",
            ),
        ),
        migrations.AddConstraint(
            model_name="teamconfirmation",
            constraint=models.CheckConstraint(
                condition=Q(purpose__in=["OPPONENT", "VOTER"]),
                name="team_confirmation_purpose_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="teamconfirmation",
            constraint=models.CheckConstraint(
                condition=Q(response__in=["PENDING", "ACCEPTED", "REJECTED"]),
                name="team_confirmation_response_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="teamconfirmation",
            constraint=models.CheckConstraint(
                condition=(
                    Q(
                        response="PENDING",
                        responded_by__isnull=True,
                        responded_at__isnull=True,
                    )
                    | Q(
                        response__in=["ACCEPTED", "REJECTED"],
                        responded_by__isnull=False,
                        responded_at__isnull=False,
                    )
                ),
                name="team_confirmation_response_evidence",
            ),
        ),
    ]
