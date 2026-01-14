from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from booking.models import Trip, Seat


def row_letters(n: int):
    # A, B, C, ... (cukup untuk bus normal)
    for i in range(n):
        yield chr(ord("A") + i)


class Command(BaseCommand):
    help = "Generate seat records for a trip (e.g., 2-2 layout -> seats_per_row=4)."

    def add_arguments(self, parser):
        parser.add_argument("--trip", type=int, required=True, help="Trip ID")
        parser.add_argument(
            "--rows",
            type=int,
            required=True,
            help="Jumlah baris kursi (A..)",
        )
        parser.add_argument(
            "--seats-per-row",
            type=int,
            required=True,
            help="Jumlah kursi per baris (mis. 4 untuk layout 2-2)",
        )
        parser.add_argument(
            "--prefix",
            type=str,
            default="",
            help="Prefix opsional untuk kode kursi (mis. 'L' => LA1, LA2...)",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Jika di-set, hapus semua seats trip ini lalu generate ulang.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        trip_id = options["trip"]
        rows = options["rows"]
        seats_per_row = options["seats_per_row"]
        prefix = (options["prefix"] or "").strip().upper()
        reset = bool(options["reset"])

        if rows <= 0 or seats_per_row <= 0:
            raise CommandError("--rows dan --seats-per-row harus > 0")

        try:
            trip = Trip.objects.get(id=trip_id)
        except Trip.DoesNotExist:
            raise CommandError(f"Trip id={trip_id} tidak ditemukan.")

        if reset:
            deleted, _ = Seat.objects.filter(trip=trip).delete()
            self.stdout.write(self.style.WARNING(f"Reset: hapus {deleted} seat."))

        existing_codes = set(
            Seat.objects.filter(trip=trip).values_list("code", flat=True)
        )

        seats_to_create = []
        created_count = 0

        for row in row_letters(rows):
            for num in range(1, seats_per_row + 1):
                code = f"{prefix}{row}{num}"
                if code in existing_codes:
                    continue
                seats_to_create.append(
                    Seat(trip=trip, code=code, status=Seat.Status.AVAILABLE)
                )

        if seats_to_create:
            Seat.objects.bulk_create(seats_to_create, batch_size=500)
            created_count = len(seats_to_create)

        # update capacity_total agar konsisten (optional)
        total_seats = Seat.objects.filter(trip=trip).count()
        if trip.capacity_total != total_seats:
            trip.capacity_total = total_seats
            trip.save(update_fields=["capacity_total"])

        self.stdout.write(self.style.SUCCESS(
            f"Selesai. Dibuat {created_count} seat baru. Total seat trip sekarang: {total_seats}."
        ))
