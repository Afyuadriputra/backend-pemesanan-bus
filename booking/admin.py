from django.contrib import admin
from .models import Trip, Seat


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "bus_type",
        "route_from",
        "route_to",
        "depart_at",
        "price",
        "capacity_total",
        "admin_wa",  # ✅ NEW
        "is_active",
    )
    list_filter = ("bus_type", "is_active")
    search_fields = ("title", "route_from", "route_to", "admin_wa")


@admin.register(Seat)
class SeatAdmin(admin.ModelAdmin):
    list_display = (
        "trip",
        "code",
        "status",
        "hold_until",
        "customer_name",
        "customer_wa",
        "claim_code",
        "booking_code",  # ✅ NEW
        "booked_at",
    )
    list_filter = ("status", "trip")
    search_fields = ("code", "customer_name", "customer_wa", "claim_code", "booking_code")
