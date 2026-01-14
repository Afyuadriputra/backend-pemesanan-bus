from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Optional

from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from .models import Seat, Trip

HOLD_MINUTES_DEFAULT = 10
MAX_HOLD_PER_SESSION_DEFAULT = 4


@dataclass
class ServiceResult:
    ok: bool
    message: str
    data: dict | None = None


def _now():
    return timezone.now()


# -----------------------------
# Housekeeping
# -----------------------------
def expire_holds() -> int:
    """
    Release semua kursi HOLD yang sudah lewat hold_until.
    Return: jumlah seat yang direlease.
    """
    now = _now()
    qs = Seat.objects.filter(status=Seat.Status.HOLD, hold_until__lt=now)
    updated = qs.update(
        status=Seat.Status.AVAILABLE,
        hold_token=None,
        hold_until=None,
        customer_name=None,
        customer_wa=None,
        claim_code=None,
        # booking_code tidak dihapus di expire (karena hanya ada saat BOOKED)
    )
    return updated


# -----------------------------
# Queries
# -----------------------------
def list_trips() -> Iterable[Trip]:
    return Trip.objects.filter(is_active=True).order_by("depart_at")


def get_trip_with_seats(trip_id: int) -> Optional[Trip]:
    try:
        return Trip.objects.prefetch_related("seats").get(id=trip_id, is_active=True)
    except Trip.DoesNotExist:
        return None


# -----------------------------
# Public API payloads
# -----------------------------
def get_seat_map(trip_id: int) -> ServiceResult:
    expire_holds()

    trip = get_trip_with_seats(trip_id)
    if not trip:
        return ServiceResult(False, "Trip tidak ditemukan.")

    seats = trip.seats.order_by("code").all()
    data = {
        "trip": {
            "id": trip.id,
            "title": trip.title,
            "bus_type": trip.bus_type,
            "route_from": trip.route_from,
            "route_to": trip.route_to,
            "depart_at": trip.depart_at.isoformat(),
            "price": trip.price,
            "capacity_total": trip.capacity_total,
            "admin_wa": getattr(trip, "admin_wa", "") or "",  # ✅ versi B
        },
        "seats": [
            {
                "id": s.id,
                "code": s.code,
                "status": s.status,
                "hold_until": s.hold_until.isoformat() if s.hold_until else None,
                # booking_code tidak perlu ditampilkan public (opsional)
            }
            for s in seats
        ],
    }
    return ServiceResult(True, "OK", data=data)


# -----------------------------
# Hold logic
# -----------------------------
def _count_holds_for_token(trip_id: int, hold_token: str) -> int:
    now = _now()
    return Seat.objects.filter(
        trip_id=trip_id,
        status=Seat.Status.HOLD,
        hold_token=hold_token,
        hold_until__gte=now,
    ).count()


@transaction.atomic
def hold_seat(
    trip_id: int,
    seat_code: str,
    hold_token: str,
    hold_minutes: int = HOLD_MINUTES_DEFAULT,
    max_hold_per_session: int = MAX_HOLD_PER_SESSION_DEFAULT,
) -> ServiceResult:
    """
    Hold kursi secara atomic (anti dobel hold).
    """
    expire_holds()
    now = _now()

    # limit jumlah kursi yang bisa di-hold per token per trip
    current_holds = _count_holds_for_token(trip_id, hold_token)
    if current_holds >= max_hold_per_session:
        return ServiceResult(False, f"Maksimal hold {max_hold_per_session} kursi.")

    try:
        seat = (
            Seat.objects.select_for_update()
            .select_related("trip")
            .get(trip_id=trip_id, code=seat_code)
        )
    except Seat.DoesNotExist:
        return ServiceResult(False, "Kursi tidak ditemukan.")

    if seat.status == Seat.Status.BOOKED:
        return ServiceResult(False, "Kursi sudah terisi (BOOKED).")

    # sedang di-hold oleh orang lain dan belum expired
    if seat.status == Seat.Status.HOLD and seat.hold_until and seat.hold_until >= now:
        if seat.hold_token != hold_token:
            return ServiceResult(False, "Kursi sedang di-hold user lain.")

        # token sama -> refresh hold
        seat.hold_until = now + timedelta(minutes=hold_minutes)
        seat.save(update_fields=["hold_until", "updated_at"])
        return ServiceResult(True, "Hold diperpanjang.", data=_seat_payload(seat))

    # available / hold expired -> ambil
    seat.status = Seat.Status.HOLD
    seat.hold_token = hold_token
    seat.hold_until = now + timedelta(minutes=hold_minutes)

    # bersihkan data lama (safety)
    seat.customer_name = None
    seat.customer_wa = None
    seat.claim_code = None
    seat.booked_at = None
    seat.booking_code = None  # ✅ versi B: jangan ada booking_code kalau belum BOOKED

    seat.save()
    return ServiceResult(True, "Kursi berhasil di-hold.", data=_seat_payload(seat))


@transaction.atomic
def release_seat(trip_id: int, seat_code: str, hold_token: str) -> ServiceResult:
    """
    User release kursi yang dia hold sendiri.
    """
    expire_holds()
    now = _now()

    try:
        seat = Seat.objects.select_for_update().get(trip_id=trip_id, code=seat_code)
    except Seat.DoesNotExist:
        return ServiceResult(False, "Kursi tidak ditemukan.")

    if seat.status != Seat.Status.HOLD or not seat.hold_until or seat.hold_until < now:
        return ServiceResult(False, "Kursi tidak sedang di-hold aktif.")

    if seat.hold_token != hold_token:
        return ServiceResult(False, "Tidak punya akses untuk melepas hold ini.")

    seat.status = Seat.Status.AVAILABLE
    seat.hold_token = None
    seat.hold_until = None
    seat.customer_name = None
    seat.customer_wa = None
    seat.claim_code = None
    seat.save()

    return ServiceResult(True, "Hold dilepas.", data=_seat_payload(seat))


# -----------------------------
# Contact + Claim
# -----------------------------
@transaction.atomic
def attach_contact_and_generate_claim(
    trip_id: int,
    hold_token: str,
    customer_name: str,
    customer_wa: str,
) -> ServiceResult:
    """
    Setelah user isi form:
    - tempelkan nama+WA ke semua seat yang dia hold (aktif)
    - generate claim_code (1 kode untuk semua seat hold user pada trip tsb)
    - return admin_wa supaya frontend bisa redirect ke WA admin (versi B)
    """
    expire_holds()
    now = _now()

    # ambil trip untuk admin_wa (versi B)
    try:
        trip = Trip.objects.get(id=trip_id, is_active=True)
    except Trip.DoesNotExist:
        return ServiceResult(False, "Trip tidak ditemukan.")

    seats = (
        Seat.objects.select_for_update()
        .filter(
            trip_id=trip_id,
            status=Seat.Status.HOLD,
            hold_token=hold_token,
            hold_until__gte=now,
        )
        .order_by("code")
    )

    if not seats.exists():
        return ServiceResult(False, "Tidak ada kursi hold aktif untuk token ini.")

    claim_code = Seat.generate_claim_code()

    seats.update(
        customer_name=customer_name.strip(),
        customer_wa=customer_wa.strip(),
        claim_code=claim_code,
    )

    seat_codes = list(seats.values_list("code", flat=True))
    hold_until_max = seats.aggregate(mx=models.Max("hold_until"))["mx"]  # type: ignore

    return ServiceResult(
        True,
        "Kontak disimpan & claim code dibuat.",
        data={
            "claim_code": claim_code,
            "seat_codes": seat_codes,
            "hold_until": hold_until_max.isoformat() if hold_until_max else None,
            "admin_wa": getattr(trip, "admin_wa", "") or "",  # ✅ versi B
        },
    )


@transaction.atomic
def claim_hold_by_code(
    trip_id: int,
    claim_code: str,
    new_hold_token: str,
    customer_wa: str | None = None,
) -> ServiceResult:
    """
    Klaim ulang hold menggunakan claim_code (dan opsional cocokkan nomor WA).
    Pindahkan hold_token ke token baru (browser/device baru).
    """
    expire_holds()
    now = _now()

    q = Q(
        trip_id=trip_id,
        status=Seat.Status.HOLD,
        hold_until__gte=now,
        claim_code=claim_code.strip().upper(),
    )
    if customer_wa:
        q &= Q(customer_wa=customer_wa.strip())

    seats = Seat.objects.select_for_update().filter(q).order_by("code")
    if not seats.exists():
        return ServiceResult(False, "Claim code tidak valid atau sudah expired.")

    seats.update(hold_token=new_hold_token)

    seat_codes = list(seats.values_list("code", flat=True))
    hold_until_max = seats.aggregate(mx=models.Max("hold_until"))["mx"]  # type: ignore

    return ServiceResult(
        True,
        "Hold berhasil di-claim.",
        data={
            "seat_codes": seat_codes,
            "hold_until": hold_until_max.isoformat() if hold_until_max else None,
        },
    )


# -----------------------------
# Admin: BOOKED + booking_code (Versi B)
# -----------------------------
@transaction.atomic
def admin_generate_booking_code_and_book(trip_id: int, seat_codes: list[str]) -> ServiceResult:
    """
    Versi B:
    - Admin generate booking_code final
    - Set seat status menjadi BOOKED + booked_at
    - booking_code disimpan di seat
    """
    expire_holds()
    now = _now()

    seats = Seat.objects.select_for_update().filter(trip_id=trip_id, code__in=seat_codes)
    if seats.count() != len(seat_codes):
        return ServiceResult(False, "Ada kursi yang tidak ditemukan.")

    already_booked = list(seats.filter(status=Seat.Status.BOOKED).values_list("code", flat=True))
    if already_booked:
        return ServiceResult(False, f"Kursi sudah BOOKED: {', '.join(already_booked)}")

    booking_code = Seat.generate_booking_code()

    seats.update(
        status=Seat.Status.BOOKED,
        booked_at=now,
        booking_code=booking_code,
        hold_token=None,
        hold_until=None,
    )

    return ServiceResult(
        True,
        "BOOKED + booking code dibuat.",
        data={"seat_codes": seat_codes, "booking_code": booking_code},
    )


# -----------------------------
# Legacy admin confirm (optional)
# -----------------------------
@transaction.atomic
def confirm_booked_by_admin(trip_id: int, seat_codes: list[str]) -> ServiceResult:
    """
    Endpoint lama: hanya set BOOKED tanpa booking_code.
    Masih boleh dipakai, tapi untuk versi B lebih baik gunakan admin_generate_booking_code_and_book().
    """
    expire_holds()
    now = _now()

    seats = Seat.objects.select_for_update().filter(trip_id=trip_id, code__in=seat_codes)
    if seats.count() != len(seat_codes):
        return ServiceResult(False, "Ada kursi yang tidak ditemukan.")

    already_booked = list(seats.filter(status=Seat.Status.BOOKED).values_list("code", flat=True))
    if already_booked:
        return ServiceResult(False, f"Kursi sudah BOOKED: {', '.join(already_booked)}")

    seats.update(
        status=Seat.Status.BOOKED,
        booked_at=now,
        hold_token=None,
        hold_until=None,
    )

    return ServiceResult(True, "Kursi berhasil dikonfirmasi BOOKED.", data={"seat_codes": seat_codes})


def _seat_payload(seat: Seat) -> dict:
    return {
        "id": seat.id,
        "trip_id": seat.trip_id,
        "code": seat.code,
        "status": seat.status,
        "hold_until": seat.hold_until.isoformat() if seat.hold_until else None,
        "claim_code": seat.claim_code,
        "booking_code": getattr(seat, "booking_code", None),  # ✅ versi B (boleh dikirim, tapi tidak wajib)
    }
