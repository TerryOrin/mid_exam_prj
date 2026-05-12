import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from water.models import Pond, SensorReading

PONDS = [
    {"name": "1 號池", "species": "鱸魚", "description": "近岸養殖區"},
    {"name": "2 號池", "species": "鱸魚", "description": "增氧設備測試區"},
    {"name": "3 號池", "species": "白蝦", "description": "混養示範區"},
]

DAYS_BACK = 30
READINGS_PER_DAY = 4


class Command(BaseCommand):
    help = "Seed demo water quality data for the AIOT assistant page."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing pond data first.")

    def handle(self, *args, **options):
        if options["reset"]:
            SensorReading.objects.all().delete()
            Pond.objects.all().delete()
            self.stdout.write(self.style.WARNING("Cleared existing water data."))

        ponds = []
        for spec in PONDS:
            pond, _ = Pond.objects.get_or_create(name=spec["name"], defaults=spec)
            ponds.append(pond)

        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        rng = random.Random(42)
        created = 0

        for pond in ponds:
            for day_offset in range(DAYS_BACK):
                day = now - timedelta(days=day_offset)
                for slot in range(READINGS_PER_DAY):
                    measured_at = day.replace(hour=6 + slot * 4)
                    low_do = pond.name == "1 號池" and day_offset < 2
                    SensorReading.objects.create(
                        pond=pond,
                        measured_at=measured_at,
                        temperature=round(rng.uniform(26.0, 30.5), 1),
                        ph=round(rng.uniform(7.4, 8.8), 2),
                        dissolved_oxygen=round(
                            rng.uniform(2.8, 3.9) if low_do else rng.uniform(5.4, 7.6),
                            1,
                        ),
                        salinity=round(rng.uniform(18.0, 30.0), 1),
                    )
                    created += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(ponds)} ponds and {created} sensor readings for the AIOT demo."
            )
        )
