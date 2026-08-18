import pytest
from django.db import IntegrityError, transaction

from core.models import Season
from core.tests.factories import season

pytestmark = pytest.mark.django_db


def test_only_one_public_season():
    season(status=Season.Status.ACTIVE, name="公开赛季一")
    with pytest.raises(IntegrityError), transaction.atomic():
        season(status=Season.Status.PRE_DRAW_PUBLIC, name="公开赛季二")


def test_setup_and_archive_are_not_public():
    assert season(status=Season.Status.SETUP, name="准备赛季").is_public is False
    assert season(status=Season.Status.ARCHIVED, name="归档赛季").is_public is False
