import json
import secrets
from typing import Any

from django.conf import settings
from django.http import JsonResponse, HttpRequest
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie

from . import services

SESSION_KEY = "seat_hold_token"
ADMIN_KEY_HEADER = "X-ADMIN-KEY"


# -----------------------------
# Helpers
# -----------------------------
def _ok(data: dict | None = None, message: str = "OK", status: int = 200) -> JsonResponse:
    payload = {"ok": True, "message": message}
    if data is not None:
        payload["data"] = data
    return JsonResponse(payload, status=status)


def _err(message: str, status: int = 400, data: dict | None = None) -> JsonResponse:
    payload = {"ok": False, "message": message}
    if data is not None:
        payload["data"] = data
    return JsonResponse(payload, status=status)


def _get_or_create_hold_token(request: HttpRequest) -> str:
    token = request.session.get(SESSION_KEY)
    if not token:
        token = secrets.token_hex(16)  # 32 chars
        request.session[SESSION_KEY] = token
    return token


def _json_body(request: HttpRequest) -> tuple[dict[str, Any], JsonResponse | None]:
    raw = request.body.decode("utf-8") if request.body else ""
    if not raw.strip():
        return {}, None
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        return {}, _err("JSON body tidak valid.", status=400)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_admin_request(request: HttpRequest) -> bool:
    # 1) admin/staff session login
    user = getattr(request, "user", None)
    if user and user.is_authenticated and user.is_staff:
        return True

    # 2) API key header
    expected = getattr(settings, "ADMIN_API_KEY", "") or ""
    if not expected:
        return False

    provided = (request.headers.get(ADMIN_KEY_HEADER) or "").strip()
    return provided == expected


# -----------------------------
# Endpoints
# -----------------------------
@ensure_csrf_cookie
@require_http_methods(["GET"])
def health(request: HttpRequest):
    return _ok(message="busbooking API is running")


@require_http_methods(["GET"])
def trips_list(request: HttpRequest):
    services.expire_holds()
    trips = services.list_trips()

    data = [
        {
            "id": t.id,
            "title": t.title,
            "bus_type": t.bus_type,
            "route_from": t.route_from,
            "route_to": t.route_to,
            "depart_at": t.depart_at.isoformat(),
            "price": t.price,
            "capacity_total": t.capacity_total,
            # NOTE: admin_wa tidak wajib ditampilkan di list, tapi kalau mau bisa tambahkan
            # "admin_wa": t.admin_wa,
        }
        for t in trips
    ]

    return JsonResponse({"ok": True, "trips": data}, status=200)


@require_http_methods(["GET"])
def seat_map(request: HttpRequest, trip_id: int):
    res = services.get_seat_map(trip_id)
    if not res.ok:
        return _err(res.message, status=404)
    return _ok(data=res.data, message=res.message)


@require_http_methods(["POST"])
def hold_seat(request: HttpRequest):
    body, err = _json_body(request)
    if err:
        return err

    trip_id = _to_int(body.get("trip_id"))
    seat_code = (body.get("seat_code") or "").strip().upper()
    if not trip_id or not seat_code:
        return _err("trip_id dan seat_code wajib diisi.", status=400)

    hold_token = _get_or_create_hold_token(request)
    res = services.hold_seat(trip_id=trip_id, seat_code=seat_code, hold_token=hold_token)

    if not res.ok:
        return _err(res.message, status=409, data=res.data)
    return _ok(data=res.data, message=res.message)


@require_http_methods(["POST"])
def release_seat(request: HttpRequest):
    body, err = _json_body(request)
    if err:
        return err

    trip_id = _to_int(body.get("trip_id"))
    seat_code = (body.get("seat_code") or "").strip().upper()
    if not trip_id or not seat_code:
        return _err("trip_id dan seat_code wajib diisi.", status=400)

    hold_token = _get_or_create_hold_token(request)
    res = services.release_seat(trip_id=trip_id, seat_code=seat_code, hold_token=hold_token)

    if not res.ok:
        return _err(res.message, status=409, data=res.data)
    return _ok(data=res.data, message=res.message)


@require_http_methods(["POST"])
def attach_contact(request: HttpRequest):
    body, err = _json_body(request)
    if err:
        return err

    trip_id = _to_int(body.get("trip_id"))
    customer_name = (body.get("customer_name") or "").strip()
    customer_wa = (body.get("customer_wa") or "").strip()

    if not trip_id or not customer_name or not customer_wa:
        return _err("trip_id, customer_name, customer_wa wajib diisi.", status=400)

    hold_token = _get_or_create_hold_token(request)
    res = services.attach_contact_and_generate_claim(
        trip_id=trip_id,
        hold_token=hold_token,
        customer_name=customer_name,
        customer_wa=customer_wa,
    )

    if not res.ok:
        return _err(res.message, status=409, data=res.data)

    # data sudah termasuk: claim_code, seat_codes, hold_until, admin_wa (versi B)
    return _ok(data=res.data, message=res.message)


@require_http_methods(["POST"])
def claim_hold(request: HttpRequest):
    body, err = _json_body(request)
    if err:
        return err

    trip_id = _to_int(body.get("trip_id"))
    claim_code = (body.get("claim_code") or "").strip().upper()
    customer_wa = (body.get("customer_wa") or "").strip()

    if not trip_id or not claim_code:
        return _err("trip_id dan claim_code wajib diisi.", status=400)

    new_token = _get_or_create_hold_token(request)
    res = services.claim_hold_by_code(
        trip_id=trip_id,
        claim_code=claim_code,
        new_hold_token=new_token,
        customer_wa=customer_wa or None,
    )

    if not res.ok:
        return _err(res.message, status=409, data=res.data)

    return _ok(data=res.data, message=res.message)


# -----------------------------
# Admin endpoints (Versi B)
# -----------------------------
@require_http_methods(["POST"])
def admin_generate_booking_code(request: HttpRequest):
    """
    Admin generate booking_code final + set kursi BOOKED.
    Akses:
    - staff session login, atau
    - header X-ADMIN-KEY sama dengan settings.ADMIN_API_KEY
    """
    if not _is_admin_request(request):
        return _err("Forbidden", status=403)

    body, err = _json_body(request)
    if err:
        return err

    trip_id = _to_int(body.get("trip_id"))
    seat_codes = body.get("seat_codes")

    if not trip_id or not isinstance(seat_codes, list) or not seat_codes:
        return _err("trip_id dan seat_codes(list) wajib diisi.", status=400)

    seat_codes_clean = [str(c).strip().upper() for c in seat_codes if str(c).strip()]
    if not seat_codes_clean:
        return _err("seat_codes tidak boleh kosong.", status=400)

    res = services.admin_generate_booking_code_and_book(trip_id=trip_id, seat_codes=seat_codes_clean)

    if not res.ok:
        return _err(res.message, status=409, data=res.data)

    # data: { seat_codes: [...], booking_code: "BK-XXXXXX" }
    return _ok(data=res.data, message=res.message)


@require_http_methods(["POST"])
def admin_confirm_booked(request: HttpRequest):
    """
    Endpoint lama (tanpa booking_code). Tetap tersedia jika kamu masih butuh.
    Untuk versi B, sebaiknya gunakan /api/admin/generate-booking-code/
    """
    if not _is_admin_request(request):
        return _err("Forbidden", status=403)

    body, err = _json_body(request)
    if err:
        return err

    trip_id = _to_int(body.get("trip_id"))
    seat_codes = body.get("seat_codes")

    if not trip_id or not isinstance(seat_codes, list) or not seat_codes:
        return _err("trip_id dan seat_codes(list) wajib diisi.", status=400)

    seat_codes_clean = [str(c).strip().upper() for c in seat_codes if str(c).strip()]
    if not seat_codes_clean:
        return _err("seat_codes tidak boleh kosong.", status=400)

    res = services.confirm_booked_by_admin(trip_id=trip_id, seat_codes=seat_codes_clean)

    if not res.ok:
        return _err(res.message, status=409, data=res.data)

    return _ok(data=res.data, message=res.message)


@require_http_methods(["POST"])
def expire_now(request: HttpRequest):
    released = services.expire_holds()
    return _ok(data={"released": released}, message="Expired holds released")
