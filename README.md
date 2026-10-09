# Koperasi — Modul Odoo 19

[![Odoo](https://img.shields.io/badge/Odoo-19.0-blueviolet)](https://www.odoo.com)
[![License: LGPL-3](https://img.shields.io/badge/License-LGPL--3-blue.svg)](https://www.gnu.org/licenses/lgpl-3.0)
[![Version](https://img.shields.io/badge/version-19.0.2.8.0-green)]()

Platform manajemen **Simpan Pinjam Koperasi Pegawai** lintas mitra, dibangun sebagai modul Odoo 19.  
Operator pusat mengelola mitra (perusahaan) dan memvalidasi anggota; admin tiap mitra mendaftarkan pegawainya sebagai calon anggota koperasi.

---

## Daftar Isi

- [Fitur Utama](#fitur-utama)
- [Arsitektur & Struktur Folder](#arsitektur--struktur-folder)
- [Model Data](#model-data)
- [Alur Kerja](#alur-kerja)
- [Hak Akses](#hak-akses)
- [Dashboard](#dashboard)
- [Laporan](#laporan)
- [Tagihan & Simpanan](#tagihan--simpanan)
- [Pinjaman](#pinjaman)
- [Instalasi & Upgrade](#instalasi--upgrade)
- [Konfigurasi](#konfigurasi)
- [Dependensi](#dependensi)
- [Informasi Versi](#informasi-versi)

---

## Fitur Utama

| Modul | Keterangan |
|---|---|
| 🏢 **Mitra** | Kelola perusahaan/mitra koperasi, sync otomatis ke `res.partner` |
| 👤 **Anggota** | Pendaftaran & validasi bertingkat anggota (pegawai mitra) |
| 💰 **Simpanan** | Simpanan Pokok, Wajib & Sukarela per anggota; tagihan bulanan otomatis |
| 🏦 **Pinjaman** | Multi-produk, simulasi plafon, angsuran flat, jadwal angsuran |
| 🧾 **Tagihan** | Per mitra per bulan, validasi per baris, rantai tunggakan otomatis |
| 📊 **Dashboard** | ECharts + OWL; kartu, tren 12 bulan, tabel; filter mitra & periode |
| 📄 **Laporan** | 5 laporan dalam 3 format: layar, PDF (A4 lanskap), Excel (xlsxwriter) |
| 🔐 **Hak Akses** | 2 grup bertingkat: Operator Pusat & Admin Mitra |

---

## Arsitektur & Struktur Folder

```
koperasi/
├── __manifest__.py                  # Deklarasi modul (depends: base, mail, web, auth_signup)
├── models/
│   ├── res_users.py                 # Tambah field koperasi_mitra_id ke res.users
│   ├── koperasi_mitra.py            # Model koperasi.mitra
│   ├── koperasi_anggota.py          # Model koperasi.anggota (+ alur validasi)
│   ├── koperasi_anggota_gaji.py     # Riwayat gaji anggota
│   ├── koperasi_kontak_mixin.py     # Mixin sinkronisasi ke res.partner
│   ├── koperasi_pengaturan.py       # Pengaturan global (simpok, simwa, plafon)
│   ├── koperasi_periode.py          # Master periode bulan tagihan
│   ├── koperasi_tagihan.py          # Tagihan & baris tagihan
│   ├── koperasi_tagihan_xlsx.py     # Generator Excel tagihan
│   ├── koperasi_pinjaman.py         # Produk pinjaman & pinjaman + angsuran
│   ├── koperasi_laporan.py          # Mesin laporan (5 jenis, 3 format)
│   ├── koperasi_laporan_xlsx.py     # Generator Excel laporan
│   └── koperasi_dashboard.py        # AbstractModel data dashboard
├── wizard/
│   ├── koperasi_tolak_wizard.py     # Wizard alasan penolakan (generik, pakai res_model/res_id)
│   └── koperasi_buat_tagihan_wizard.py  # Wizard buat tagihan manual
├── controllers/
│   └── main.py                      # Route unduhan: /koperasi/laporan/{pdf,xlsx}, /koperasi/tagihan/<id>/xlsx
├── report/
│   ├── koperasi_laporan_report.py   # Render PDF laporan (QWeb)
│   └── koperasi_laporan_report.xml  # Template QWeb A4 lanskap
├── security/
│   ├── koperasi_security.xml        # Grup & privilege
│   ├── ir.model.access.csv          # ACL per grup
│   └── koperasi_rule.xml            # Record rules (operator & admin mitra)
├── data/
│   ├── koperasi_sequence_data.xml   # Sekuens nomor anggota (KOP-<tahun>-00001) & pinjaman
│   └── koperasi_simpanan_data.xml   # Data pengaturan simpanan awal
├── views/
│   ├── koperasi_mitra_views.xml     # Form & list mitra
│   ├── koperasi_anggota_views.xml   # Form & list anggota
│   ├── koperasi_simpanan_views.xml  # Tagihan simpanan
│   ├── koperasi_pinjaman_views.xml  # Pinjaman & produk pinjaman
│   ├── koperasi_dashboard_views.xml # Action client dashboard
│   ├── koperasi_laporan_views.xml   # Action laporan
│   ├── res_users_views.xml          # Tab Koperasi di form res.users
│   └── menu.xml                     # Struktur menu utama
└── static/
    ├── description/icon.png         # Ikon modul
    ├── lib/echarts/echarts.min.js   # ECharts (Apache-2.0, dimuat lazy)
    └── src/
        ├── dashboard/               # Komponen OWL Dashboard (JS + XML + CSS)
        └── laporan/                 # Komponen OWL Laporan (JS + XML + CSS)
```

---

## Model Data

### `koperasi.mitra`
Mitra/perusahaan yang bekerja sama dengan koperasi.

| Field | Keterangan |
|---|---|
| `kode` | Kode otomatis `MTR-001` |
| `name` | Nama mitra |
| `admin_user_id` | User admin mitra (dibuat lewat tombol Kirim Undangan) |
| `admin_status` | belum / diundang / aktif / nonaktif |
| Alamat | `street`, `street2`, `city`, `state_id`, `zip`, `country_id` (default Indonesia) |
| `partner_id` | Kontak perusahaan Odoo (sync otomatis) |
| `anggota_count` | Smart button ke daftar anggota |

### `koperasi.anggota`
Pegawai mitra yang terdaftar sebagai anggota koperasi.

| Field | Keterangan |
|---|---|
| `nomor_anggota` | Otomatis (`KOP-<tahun>-00001`) saat divalidasi |
| `mitra_id` | Mitra tempat anggota bekerja |
| `nik`, `nip`, `jabatan` | Data pribadi & kepegawaian |
| `gaji_bersih` | Dipakai untuk hitung plafon pinjaman |
| `simsula` | Simpanan sukarela (bisa diubah admin mitra kapan pun) |
| `bank_tujuan`, `nomor_rekening` | Rekening pencairan pinjaman |
| `state` | draft → menunggu → aktif / ditolak |

### `koperasi.tagihan` & `koperasi.tagihan.line`
Tagihan simpanan per mitra per bulan.

- **UNIQUE** (mitra + periode); nomor `TGH/YYYYMM/<kode mitra>`
- Baris per anggota aktif: simpok, simwa, simsula, tunggakan, angsuran pinjaman
- Status baris: `menunggu` / `dibayar` / `belum`
- Tunggakan berantai: baris `belum` otomatis terbawa ke tagihan periode berikutnya

### `koperasi.pinjaman`
Pengajuan pinjaman anggota.

- Nomor `PJM-<tahun>-00001`
- Alur: draft → diajukan → disetujui → berjalan → lunas / ditolak
- Hanya **satu pinjaman aktif** per anggota
- Jadwal angsuran (`koperasi.pinjaman.angsuran`) dibuat otomatis saat dicairkan

### `koperasi.pinjaman.produk`
Produk/jenis pinjaman (misal: Reguler, Darurat, Pendidikan).

| Field | Keterangan |
|---|---|
| `jasa_persen` | Bunga flat per bulan (%) |
| `admin_mode` | Biaya admin: persentase / nominal |
| `tenor_min/max` | Batas tenor bulan |
| Simulasi | Panel simulasi di form: hitung angsuran & plafon langsung |

### `koperasi.pengaturan`
Konfigurasi global (satu record):

| Field | Default |
|---|---|
| `simpok` | Simpanan pokok (sekali) |
| `simwa` | Simpanan wajib (bulanan) |
| `batas_potongan` | 40% (maks potongan gaji untuk angsuran) |
| `plafon_kelipatan` | Rp 1.000.000 |

### `koperasi.periode`
Master periode bulan (`OKT 2026`). UNIQUE(bulan, tahun). Auto-generate 12 bulan ke depan via cron.

---

## Alur Kerja

### Validasi Anggota
```
draft ──[Ajukan]──▶ menunggu ──[Setujui]──▶ aktif
  ▲                    │
  └──[edit + ajukan] ◀─┴──[Tolak + alasan] ──▶ ditolak
```
- **Setujui** (Operator Pusat): beri nomor anggota, buat `res.partner`, catat validator
- **Tolak**: wajib isi alasan lewat wizard, tampil sebagai banner merah
- Admin mitra tidak bisa mengedit saat status `menunggu` atau `aktif`

### Alur Pinjaman
```
draft ──[Validasi & Ajukan (admin mitra)]──▶ diajukan
  ──[Setujui (operator)]──▶ disetujui
  ──[Cairkan (operator)]──▶ berjalan ──[angsuran lunas semua]──▶ lunas
  [Tolak]──▶ ditolak ──[perbaiki + ajukan ulang]──▶ diajukan
```

### Tagihan Bulanan
```
draft ──[Tagihkan]──▶ ditagihkan ──[validasi per baris selesai]──▶ selesai
```
- Dibuat otomatis tiap tanggal 1 (cron) atau manual lewat wizard
- Baris `belum dibayar` → otomatis jadi tunggakan di tagihan bulan berikutnya
- Baris pembawa Dibayar → seluruh rantai tunggakan otomatis Dibayar

---

## Hak Akses

| Grup | Kemampuan |
|---|---|
| **Operator Pusat** | CRUD semua mitra & anggota, validasi anggota, setujui & cairkan pinjaman, semua laporan, pengaturan |
| **Admin Mitra** | Daftar anggota mitranya (draft/ditolak saja yang bisa diedit), ajukan pinjaman, lihat tagihan yang sudah ditagihkan, laporan mitranya |

- Satu user = satu mitra (`koperasi_mitra_id`)
- Record rule memastikan admin mitra hanya melihat data mitranya sendiri
- Tab "Koperasi" di form `res.users` hanya terlihat oleh Operator Pusat
- Admin mitra dibuat via tombol **Kirim Undangan** di form mitra (email auth_signup)

---

## Dashboard

Dashboard utama (halaman awal aplikasi) berbasis **OWL + ECharts**.

**Empat bagian sidebar:** Ringkasan · Simpanan · Pinjaman · Tagihan

Setiap bagian menampilkan:
- 4 kartu angka dengan gradasi warna
- 2 grafik ECharts (tren + komposisi)
- 2 tabel ringkasan

**Filter:** Mitra (multi-mitra, tampil bila > 1 mitra terlihat) & Periode (sekarang / semua / pilih bulan)

Angka **Posisi** (saldo, anggota, tunggakan) selalu _saat ini_; angka **Aliran** (tagihan masuk, dana tersalurkan, jasa) mengikuti filter periode.

---

## Laporan

Lima jenis laporan tersedia dalam **3 format**:

| Laporan | Keterangan |
|---|---|
| 📊 Ringkasan Keuangan | Posisi & arus dana, pendapatan platform, per periode/mitra |
| 💳 Simpanan | Saldo awal/penerimaan/akhir per mitra atau per anggota |
| 🏦 Pinjaman | Rekonsiliasi piutang, per produk, daftar pinjaman |
| 🧾 Tagihan & Kolektibilitas | Per periode/mitra, tunggakan |
| 📖 Buku Simpanan Anggota | Mutasi per periode, pinjaman, angsuran |

**Format output:**
- **Layar** — Halaman OWL interaktif (`/web#action=koperasi.laporan`)
- **PDF** — QWeb A4 lanskap, layout `web.internal_layout`
- **Excel** — xlsxwriter, angka asli + rumus SUM/SUMIF, siap cetak

**Unduhan** via route `/koperasi/laporan/{pdf,xlsx}?jenis&mitra&dari&sampai&anggota`

---

## Tagihan & Simpanan

### Pembuatan Tagihan
- **Otomatis**: cron bulanan tiap tanggal 1
- **Manual**: wizard *Buat Tagihan* (pilih periode & mitra)
- Dilewati bila: `simwa = 0`, mitra tanpa anggota aktif, atau tagihan periode itu sudah ada

### Komponen Tagihan per Anggota
| Komponen | Keterangan |
|---|---|
| Simpok | Hanya tagihan pertama anggota |
| Simwa | Setiap bulan |
| Simsula | Nilai yang diisi admin mitra |
| Tunggakan | Dari baris `belum dibayar` bulan lalu |
| Angsuran pinjaman | Pokok + jasa, bila ada pinjaman berjalan |

### Saldo Simpanan
Dihitung dari baris tagihan berstatus **Dibayar**. Simpok/Simwa/Simsula dihitung terpisah.

### Unduh Excel Tagihan
Tombol **Unduh Excel** di form tagihan → file `Tagihan TGH-YYYYMM-<kode mitra>.xlsx`  
(A4 landscape, header berulang, rumus SUM, nama file otomatis)

---

## Pinjaman

### Formula (Flat Rate)
```
Jasa/bulan      = Pokok × Jasa%
Angsuran/bulan  = Pokok ÷ Tenor + Jasa/bulan
Biaya admin     = Pokok × Admin% (atau nominal tetap)
Angsuran maks   = Batas Potongan% × Gaji Bersih − Potongan Simpanan
Plafon          = Angsuran maks ÷ (1/Tenor + Jasa%) → dibulatkan ke bawah per kelipatan
```

**Contoh:** Pokok 12 jt / 12 bln / gaji 8 jt → angsuran Rp 1.120.000, admin Rp 240.000, plafon ~Rp 31 jt

### Kelayakan (dicek saat Ajukan & Setujui)
- Anggota berstatus aktif & gaji terisi
- Tenor dalam rentang produk
- Tidak ada pinjaman lain yang sedang aktif (diajukan/disetujui/berjalan)
- Angsuran ≤ angsuran maksimum
- Pokok ≤ plafon

### Angsuran
- Jadwal dibuat saat pinjaman **Dicairkan**
- Angsuran pertama = periode setelah bulan pencairan
- Status angsuran mengikuti status baris tagihan (`terjadwal` → `ditagihkan` → `dibayar`)
- Pinjaman otomatis **Lunas** bila semua angsuran dibayar

---

## Instalasi & Upgrade

### Instalasi Pertama
```bash
# Dari folder server/ Odoo
python odoo-bin -c odoo.conf -d <nama_database> -i koperasi --stop-after-init
```

### Upgrade Modul
```bash
python odoo-bin -c odoo.conf -d <nama_database> -u koperasi --stop-after-init
```
Setelah upgrade, restart service:
```bash
# Windows (jika pakai service)
net stop odoo-server-19.0 && net start odoo-server-19.0
```
> **Catatan:** Jika browser menampilkan error cache setelah upgrade, lakukan **Ctrl+Shift+R** atau hapus site data `localhost:8069`.

---

## Konfigurasi

Setelah instalasi, buka menu **Koperasi → Pengaturan → Pengaturan Simpanan** (Operator Pusat):

| Setting | Keterangan |
|---|---|
| Simpanan Pokok | Nominal simpanan pokok (sekali saat anggota divalidasi) |
| Simpanan Wajib | Nominal simpanan wajib per bulan |
| Batas Potongan Gaji | Persentase maksimum potongan gaji untuk angsuran (default 40%) |
| Kelipatan Plafon | Pembulatan plafon pinjaman (default Rp 1.000.000) |

**Periode:** Menu **Pengaturan → Periode** — tambah 12 bulan berikutnya dengan satu tombol.

**Produk Pinjaman:** Menu **Pengaturan → Produk Pinjaman** — buat produk dengan jasa%, tenor, dan biaya admin.

**Tambah Mitra:** Menu **Mitra** → Baru → isi data → **Kirim Undangan** (untuk akun admin mitra).

---

## Dependensi

| Dependensi | Keterangan |
|---|---|
| `base` | Model dasar Odoo |
| `mail` | `mail.thread`, chatter, aktivitas |
| `web` | OWL, assets frontend |
| `auth_signup` | Undangan email untuk admin mitra |
| `xlsxwriter` | Generator Excel (sudah bawaan Odoo) |
| `ECharts 5` | Grafik dashboard (Apache-2.0, bundled di `static/lib/`) |

---

## Informasi Versi

| Versi | Tanggal | Perubahan |
|---|---|---|
| 19.0.1.1.0 | 2026-10-04 | Penyesuaian menu, label, dan form mitra |
| 19.0.1.3.0 | 2026-10-04 | Form mitra: group kiri/kanan, tombol Kirim Undangan |
| 19.0.1.4.0 | 2026-10-04 | `koperasi.kontak.mixin`, form anggota baru |
| 19.0.1.5.0 | 2026-10-04 | Gaji anggota + riwayat gaji |
| 19.0.1.6.0 | 2026-10-06 | Simpanan, tagihan, pengaturan simpanan |
| 19.0.1.7.0 | 2026-10-06 | Master periode |
| 19.0.1.8.0 | 2026-10-06 | Validasi per baris, tunggakan berantai |
| 19.0.1.9.0 | 2026-10-06 | Unduh Excel tagihan |
| 19.0.2.0.0 | 2026-10-07 | Pinjaman & angsuran |
| 19.0.2.1.0 | 2026-10-07 | Smart button anggota, tab Simpanan |
| 19.0.2.2.0 | 2026-10-07 | Rekening pencairan di form anggota |
| 19.0.2.3.0 | 2026-10-07 | Info rekening di form pinjaman |
| 19.0.2.4.0 | 2026-10-07 | Smart button mitra |
| 19.0.2.5.0 | 2026-10-07 | Dashboard OWL + ECharts |
| 19.0.2.6.0 | 2026-10-07 | Filter periode dashboard |
| 19.0.2.8.0 | 2026-10-08 | Laporan (5 jenis, 3 format) |

---

## Lisensi

[LGPL-3.0](https://www.gnu.org/licenses/lgpl-3.0.html)

---

**Dibuat oleh:** [CV Sel Studio](https://selstudio.id)  
**Kompatibel dengan:** Odoo 19.0
