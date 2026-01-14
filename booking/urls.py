from django.urls import path
from . import views

urlpatterns = [
    # =============================
    # Health / CSRF prime
    # =============================
    path("health/", views.health),

    # =============================
    # Public API (User)
    # =============================
    path("api/trips/", views.trips_list),
    path("api/trips/<int:trip_id>/seats/", views.seat_map),

    # Seat hold flow
    path("api/seats/hold/", views.hold_seat),
    path("api/seats/release/", views.release_seat),

    # Contact & claim
    path("api/hold/attach-contact/", views.attach_contact),
    path("api/hold/claim/", views.claim_hold),

    # =============================
    # Admin API (Versi B)
    # =============================
    # Admin generate booking code + set BOOKED
    path(
        "api/admin/generate-booking-code/",
        views.admin_generate_booking_code,
    ),

    # (Legacy / optional)
    path(
        "api/admin/confirm-booked/",
        views.admin_confirm_booked,
    ),

    # =============================
    # Housekeeping
    # =============================
    path("api/expire/", views.expire_now),
]
