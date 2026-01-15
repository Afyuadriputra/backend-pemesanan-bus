# Analisis Sistem: Backend Pemesanan Bus

## Ringkasan
Berdasarkan analisis basis kode di `D:\Kuliah\joki\joki radit\sistem bus\backend`, sistem ini adalah API REST berbasis Django untuk mengelola pemesanan perjalanan bus. Sistem ini memungkinkan pengguna untuk melihat perjalanan, menahan (hold) kursi sementara, dan menyelesaikan pemesanan melalui proses persetujuan admin (kemungkinan melibatkan komunikasi WhatsApp).

## Komponen Utama

### 1. Model Data (`booking/models.py`)
- **Trip**: Merepresentasikan perjalanan bus yang dijadwalkan.
    - Field: `route_from` (Asal), `route_to` (Tujuan), `depart_at` (Waktu Berangkat), `price` (Harga), `bus_type` (Ekonomi/Executive/Sleeper), `admin_wa` (WhatsApp Admin).
- **Seat**: Merepresentasikan kursi spesifik pada sebuah perjalanan.
    - Field: `code` (contoh: "1A"), `status` (AVAILABLE, HOLD, BOOKED).
    - Kunci Siklus Hidup:
        - `hold_token`: Kunci sesi sementara.
        - `claim_code`: Kode bagi pengguna untuk melanjutkan sesi/mengklaim kursi.
        - `booking_code`: Kode konfirmasi final yang dibuat oleh admin.

### 2. Logika Bisnis & Alur Kerja (`booking/services.py`)

Proses pemesanan mengikuti alur "Tahan-lalu-Pesan" (*Hold-then-Book*):

#### Fase 1: Pemilihan & Penahanan Kursi (Hold)
1.  **Pencarian**: Pengguna melihat daftar perjalanan (`GET /api/trips/`).
2.  **Lihat Peta**: Pengguna melihat ketersediaan kursi untuk perjalanan tertentu (`GET /api/trips/{id}/seats/`).
3.  **Hold**: Pengguna memilih kursi (`POST /api/seats/hold/`).
    -   **Aksi**: Transaksi atomik mengunci kursi selama **10 menit** (`HOLD_MINUTES_DEFAULT`).
    -   **Batasan**: Maksimal **4 kursi** per sesi.
    -   **Mekanisme**: Menggunakan `seat_hold_token` yang disimpan di sesi pengguna (cookie).

#### Fase 2: Informasi Kontak
4.  **Lampirkan Kontak**: Pengguna mengirimkan nama dan WhatsApp (`POST /api/hold/attach-contact/`).
    -   **Aksi**: Memperbarui kursi yang ditahan dengan info pelanggan.
    -   **Hasil**: Menghasilkan **Kode Klaim** (contoh: "A1B2-C3D4") dan mengembalikan nomor `admin_wa`.

#### Fase 3: Pembayaran & Konfirmasi
5.  **Offline/WhatsApp**: Sistem mengarahkan pengguna untuk menghubungi admin (melalui `admin_wa` yang dikembalikan) untuk menyelesaikan pembayaran, dengan memberikan Nama atau Kode Klaim mereka.
6.  **Admin Booking**: Admin mengonfirmasi pembayaran.
    -   **Aksi**: Admin memanggil `POST /api/admin/generate-booking-code/` (memerlukan login Staff atau API Key).
    -   **Hasil**: Status kursi berubah menjadi **BOOKED**, dan **Kode Booking** final (contoh: "BK-XYZ123") dibuat.

#### Fase 4: Pemulihan (Opsional)
-   **Klaim Hold**: Jika pengguna berganti perangkat atau menghapus cookie, mereka dapat menggunakan `POST /api/hold/claim/` dengan `claim_code` mereka untuk memulihkan kursi yang mereka tahan (konsepnya mirip dengan "Pulihkan Sesi").

### 3. Konfigurasi Utama (`settings.py`)
-   **Database**: SQLite (`db.sqlite3`).
-   **CORS**: Mengizinkan `localhost:5173` (Vite) dan `localhost:3000` (CRA), menandakan aplikasi Frontend React yang terpisah.
-   **Session**: Umur cookie sesi 1 jam.
-   **Keamanan**: Menggunakan `X-ADMIN-KEY` untuk melindungi endpoint admin selain otentikasi sesi standar Django.

## Ringkasan Endpoint API

| Metode | Endpoint | Deskripsi |
| :--- | :--- | :--- |
| `GET` | `/api/trips/` | Menampilkan semua perjalanan aktif. |
| `GET` | `/api/trips/<id>/seats/` | Mendapatkan peta kursi dan statusnya. |
| `POST` | `/api/seats/hold/` | Menahan kursi (kunci selama 10 menit). |
| `POST` | `/api/seats/release/` | Membatalkan penahanan secara manual. |
| `POST` | `/api/hold/attach-contact/` | Mengirimkan detail pelanggan. |
| `POST` | `/api/admin/generate-booking-code/` | **(Admin)** Menyelesaikan pemesanan & membuat kode. |
