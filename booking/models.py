from django.db import models
import secrets


class Trip(models.Model):
    BUS_TYPE_CHOICES = [
        ("EKONOMI", "Ekonomi"),
        ("EXEC", "Executive"),
        ("SLEEPER", "Sleeper"),
    ]

    title = models.CharField(max_length=120)
    bus_type = models.CharField(max_length=20, choices=BUS_TYPE_CHOICES)

    route_from = models.CharField(max_length=80)
    route_to = models.CharField(max_length=80)

    depart_at = models.DateTimeField()
    price = models.PositiveIntegerField()

    description = models.TextField(blank=True)

    capacity_total = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    # ✅ NEW: admin WhatsApp (format internasional tanpa "+", contoh: 62812xxxx)
    admin_wa = models.CharField(max_length=30, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} ({self.route_from}→{self.route_to})"


class Seat(models.Model):
    class Status(models.TextChoices):
        AVAILABLE = "AVAILABLE", "Available"
        HOLD = "HOLD", "Hold"
        BOOKED = "BOOKED", "Booked"

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="seats")
    code = models.CharField(max_length=10)

    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AVAILABLE)

    hold_token = models.CharField(max_length=64, blank=True, null=True)
    hold_until = models.DateTimeField(blank=True, null=True)

    customer_name = models.CharField(max_length=120, blank=True, null=True)
    customer_wa = models.CharField(max_length=30, blank=True, null=True)

    claim_code = models.CharField(max_length=20, blank=True, null=True)

    booked_at = models.DateTimeField(blank=True, null=True)

    # ✅ NEW: kode booking final yang dibuat admin
    booking_code = models.CharField(max_length=30, blank=True, null=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("trip", "code")
        indexes = [
            models.Index(fields=["trip", "status"]),
            models.Index(fields=["status", "hold_until"]),
            models.Index(fields=["claim_code"]),
            models.Index(fields=["booking_code"]),  # ✅ optional, tapi bagus
        ]

    def __str__(self):
        return f"{self.trip_id}:{self.code} [{self.status}]"

    @staticmethod
    def generate_claim_code() -> str:
        raw = secrets.token_hex(4).upper()
        return f"{raw[:4]}-{raw[4:]}"

    @staticmethod
    def generate_booking_code() -> str:
        # Contoh: BK-9A2F3C
        raw = secrets.token_hex(3).upper()
        return f"BK-{raw}"
