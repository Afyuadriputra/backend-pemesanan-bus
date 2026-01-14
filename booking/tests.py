from datetime import timedelta
from django.test import TestCase, Client
from django.utils import timezone

from .models import Trip, Seat
from . import services


class BookingServiceTests(TestCase):
    def setUp(self):
        self.trip = Trip.objects.create(
            title="Jakarta → Bandung (Pagi)",
            bus_type="EXEC",
            route_from="Jakarta",
            route_to="Bandung",
            depart_at=timezone.now() + timedelta(days=1),
            price=150000,
            capacity_total=4,
            is_active=True,
        )
        # Buat 4 kursi untuk test
        Seat.objects.create(trip=self.trip, code="A1")
        Seat.objects.create(trip=self.trip, code="A2")
        Seat.objects.create(trip=self.trip, code="A3")
        Seat.objects.create(trip=self.trip, code="A4")

        self.token_a = "tokenA"
        self.token_b = "tokenB"

    def test_hold_seat_success(self):
        res = services.hold_seat(self.trip.id, "A1", self.token_a)
        self.assertTrue(res.ok)

        seat = Seat.objects.get(trip=self.trip, code="A1")
        self.assertEqual(seat.status, Seat.Status.HOLD)
        self.assertEqual(seat.hold_token, self.token_a)
        self.assertIsNotNone(seat.hold_until)
        self.assertGreater(seat.hold_until, timezone.now())

    def test_hold_conflict_other_token(self):
        services.hold_seat(self.trip.id, "A1", self.token_a)
        res = services.hold_seat(self.trip.id, "A1", self.token_b)
        self.assertFalse(res.ok)

        seat = Seat.objects.get(trip=self.trip, code="A1")
        self.assertEqual(seat.status, Seat.Status.HOLD)
        self.assertEqual(seat.hold_token, self.token_a)

    def test_hold_expired_can_be_taken(self):
        # set A1 jadi HOLD tapi expired
        seat = Seat.objects.get(trip=self.trip, code="A1")
        seat.status = Seat.Status.HOLD
        seat.hold_token = self.token_a
        seat.hold_until = timezone.now() - timedelta(minutes=1)
        seat.save()

        # token B coba hold -> harus sukses
        res = services.hold_seat(self.trip.id, "A1", self.token_b)
        self.assertTrue(res.ok)

        seat.refresh_from_db()
        self.assertEqual(seat.status, Seat.Status.HOLD)
        self.assertEqual(seat.hold_token, self.token_b)

    def test_max_hold_per_session(self):
        # hold 4 kursi
        for code in ["A1", "A2", "A3", "A4"]:
            res = services.hold_seat(self.trip.id, code, self.token_a, max_hold_per_session=4)
            self.assertTrue(res.ok)

        # kursi ke-5 (kita buat tambahan dulu)
        Seat.objects.create(trip=self.trip, code="B1")
        res = services.hold_seat(self.trip.id, "B1", self.token_a, max_hold_per_session=4)
        self.assertFalse(res.ok)

    def test_attach_contact_generates_claim_code(self):
        services.hold_seat(self.trip.id, "A1", self.token_a)
        services.hold_seat(self.trip.id, "A2", self.token_a)

        res = services.attach_contact_and_generate_claim(
            trip_id=self.trip.id,
            hold_token=self.token_a,
            customer_name="Budi",
            customer_wa="08123456789",
        )
        self.assertTrue(res.ok)
        self.assertIn("claim_code", res.data)
        claim_code = res.data["claim_code"]

        s1 = Seat.objects.get(trip=self.trip, code="A1")
        s2 = Seat.objects.get(trip=self.trip, code="A2")
        self.assertEqual(s1.customer_name, "Budi")
        self.assertEqual(s1.customer_wa, "08123456789")
        self.assertEqual(s1.claim_code, claim_code)
        self.assertEqual(s2.claim_code, claim_code)

    def test_claim_hold_moves_token(self):
        services.hold_seat(self.trip.id, "A1", self.token_a)
        attach = services.attach_contact_and_generate_claim(
            trip_id=self.trip.id,
            hold_token=self.token_a,
            customer_name="Budi",
            customer_wa="08123456789",
        )
        claim_code = attach.data["claim_code"]

        res = services.claim_hold_by_code(
            trip_id=self.trip.id,
            claim_code=claim_code,
            new_hold_token=self.token_b,
            customer_wa="08123456789",
        )
        self.assertTrue(res.ok)

        seat = Seat.objects.get(trip=self.trip, code="A1")
        self.assertEqual(seat.status, Seat.Status.HOLD)
        self.assertEqual(seat.hold_token, self.token_b)

    def test_confirm_booked_by_admin(self):
        services.hold_seat(self.trip.id, "A1", self.token_a)

        res = services.confirm_booked_by_admin(self.trip.id, ["A1"])
        self.assertTrue(res.ok)

        seat = Seat.objects.get(trip=self.trip, code="A1")
        self.assertEqual(seat.status, Seat.Status.BOOKED)
        self.assertIsNotNone(seat.booked_at)
        self.assertIsNone(seat.hold_token)
        self.assertIsNone(seat.hold_until)

    def test_expire_holds_releases_seat(self):
        seat = Seat.objects.get(trip=self.trip, code="A1")
        seat.status = Seat.Status.HOLD
        seat.hold_token = self.token_a
        seat.hold_until = timezone.now() - timedelta(minutes=1)
        seat.customer_name = "Budi"
        seat.customer_wa = "08"
        seat.claim_code = "AAAA-BBBB"
        seat.save()

        released = services.expire_holds()
        self.assertGreaterEqual(released, 1)

        seat.refresh_from_db()
        self.assertEqual(seat.status, Seat.Status.AVAILABLE)
        self.assertIsNone(seat.hold_token)
        self.assertIsNone(seat.hold_until)
        self.assertIsNone(seat.customer_name)
        self.assertIsNone(seat.customer_wa)
        self.assertIsNone(seat.claim_code)


class BookingViewsTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.trip = Trip.objects.create(
            title="Jakarta → Bandung (Pagi)",
            bus_type="EXEC",
            route_from="Jakarta",
            route_to="Bandung",
            depart_at=timezone.now() + timedelta(days=1),
            price=150000,
            capacity_total=1,
            is_active=True,
        )
        Seat.objects.create(trip=self.trip, code="A1")

    def test_health_endpoint(self):
        resp = self.client.get("/health/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])

    def test_hold_endpoint_success(self):
        resp = self.client.post(
            "/api/seats/hold/",
            data='{"trip_id": %d, "seat_code": "A1"}' % self.trip.id,
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        seat = Seat.objects.get(trip=self.trip, code="A1")
        self.assertEqual(seat.status, Seat.Status.HOLD)
