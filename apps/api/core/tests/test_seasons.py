import pytest
from django.db import IntegrityError, transaction

from core.models import Season
from core.tests.factories import season

pytestmark = pytest.mark.django_db


def test_only_one_public_season():
    season(status=Season.Status.PUBLISHED, name="公开赛季一")
    with pytest.raises(IntegrityError), transaction.atomic():
        season(status=Season.Status.PUBLISHED, name="公开赛季二")
