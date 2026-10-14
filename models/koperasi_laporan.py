"""Mesin laporan koperasi.

Satu struktur data standar dipakai tiga penampil: halaman di layar (OWL), PDF (QWeb)
dan Excel (xlsxwriter). Semua angka dihitung dari data operasional dengan hak pengguna
yang memanggil, tanpa sudo: admin mitra otomatis hanya mendapat mitranya, dan tagihan
Draft tidak ikut terhitung baginya.

Penerimaan (angsuran pinjaman) dihitung menurut PERIODE PENGAKUAN baris tagihan
(`periode_akui_id`): periode tagihannya sendiri bila dibayar langsung, atau periode tagihan
pembawanya bila dibayar sebagai tunggakan. Karena itu angka periode yang sudah ditutup
tidak berubah oleh pembayaran susulan.

Ini laporan manajemen turunan data operasional, BUKAN Neraca / Laba Rugi akuntansi:
aplikasi belum memiliki kas/bank, biaya operasional, dan sumber dana.
"""
from collections import defaultdict
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _

from .koperasi_periode import BULAN_SINGKAT
from .koperasi_pinjaman import STATE_PINJAMAN
from .koperasi_tagihan import STATE_TAGIHAN, STATUS_BARIS

JENIS = [
    ('ringkasan', 'Ringkasan Keuangan', 'fa-line-chart'),
    ('pinjaman', 'Laporan Pinjaman', 'fa-credit-card'),
    ('tagihan', 'Tagihan & Kolektibilitas', 'fa-file-text-o'),
    ('kualitas', 'Umur Tunggakan & Kualitas Pinjaman', 'fa-heartbeat'),
    ('proyeksi', 'Proyeksi Penerimaan', 'fa-calendar'),
    ('anggota', 'Riwayat Anggota', 'fa-address-book-o'),
    # dari jurnal pembukuan (koperasi_laporan_buku.py)
    ('neraca', 'Neraca', 'fa-balance-scale'),
    ('labarugi', 'Laba Rugi', 'fa-bar-chart'),
    ('aruskas', 'Arus Kas', 'fa-exchange'),
    ('bukubesar', 'Neraca Saldo & Jurnal', 'fa-book'),
]
# Laporan keuangan koperasi secara utuh: hanya untuk operator pusat
JENIS_PEMBUKUAN = {'neraca', 'labarugi', 'aruskas', 'bukubesar', 'shu'}
# Laporan yang hanya memakai satu periode acuan (filter "Dari periode" disembunyikan): {jenis: label filter}
SATU_PERIODE = {'kualitas': 'Posisi per periode', 'proyeksi': 'Mulai setelah periode',
                'neraca': 'Posisi per akhir periode'}
# Laporan per dokumen: tidak tampil di daftar, dibuka dari tombol di form-nya
JENIS_DOKUMEN = {'kartu', 'shu'}
BATAS_BARIS_PINJAMAN = 500
BULAN_PROYEKSI = 12
KELOMPOK_UMUR = ['Bulan berjalan', '1 bulan', '2 bulan', '3 bulan atau lebih']
# Kolektibilitas pinjaman menurut umur angsuran tertua yang belum dibayar
KATEGORI_PINJAMAN = ['Lancar', 'Dalam Perhatian', 'Kurang Lancar', 'Diragukan', 'Macet']


def buka(record):
    """Tautan drill-down: baris laporan di layar membuka form record ini."""
    return {'model': record._name, 'id': record.id}


def kolom(key, label, tipe='teks', jumlah=False):
    """tipe: teks | kode | rp | angka | persen | tanggal | status. jumlah=True -> dijumlah di baris total."""
    return {'key': key, 'label': label, 'tipe': tipe, 'jumlah': jumlah}


def bagian(judul, kolom_, baris, total=True, kosong='Tidak ada data pada periode ini.', catatan=None):
    """Satu tabel laporan. Baris total dibuat otomatis dari kolom ber-`jumlah`."""
    baris = list(baris)
    hasil = {'judul': judul, 'kolom': kolom_, 'baris': baris, 'kosong': kosong, 'catatan': catatan, 'total': None}
    kolom_jumlah = [k['key'] for k in kolom_ if k['jumlah']]
    if total and kolom_jumlah and baris:
        isi = {k: sum(b.get(k) or 0 for b in baris if not b.get('_gaya')) for k in kolom_jumlah}
        isi[kolom_[0]['key']] = _('TOTAL')
        hasil['total'] = isi
    return hasil


class KoperasiLaporan(models.AbstractModel):
    _name = 'koperasi.laporan'
    _description = 'Mesin Laporan Koperasi'

    # ------------------------------------------------------------------
    # Pilihan filter
    # ------------------------------------------------------------------
    @api.model
    def get_opsi(self):
        Periode = self.env['koperasi.periode']
        sekarang = Periode._untuk_tanggal(fields.Date.context_today(self))
        terakhir = self.env['koperasi.tagihan']._read_group(
            [], [], ['periode:max'])[0][0] or sekarang.tanggal_mulai
        batas = max(sekarang.tanggal_mulai, terakhir)
        periode = Periode.search([('tanggal_mulai', '<=', batas)], order='tanggal_mulai')
        awal_tahun = periode.filtered(lambda p: p.tahun == sekarang.tahun and p.bulan == '1')
        operator = self.env.user.has_group('koperasi.group_koperasi_operator')
        jenis = [{'id': j, 'label': n, 'ikon': i, 'rentang': j not in SATU_PERIODE,
                  'label_sampai': SATU_PERIODE.get(j, 'Sampai periode'),
                  'kelompok': 'Pembukuan' if j in JENIS_PEMBUKUAN else 'Laporan'}
                 for j, n, i in JENIS if operator or j not in JENIS_PEMBUKUAN]
        return {
            'jenis': jenis,
            # judul bagian di sidebar, urut seperti kemunculannya
            'kelompok': list(dict.fromkeys(j['kelompok'] for j in jenis)),
            'is_operator': operator,
            'mitra_list': [{'id': m.id, 'name': m.name} for m in self.env['koperasi.mitra'].search([])],
            'periode_list': [{'id': p.id, 'name': p.name} for p in periode],
            'dari': (awal_tahun or periode[:1]).id,
            'sampai': sekarang.id,
        }

    @api.model
    def daftar_anggota(self, mitra_id=False):
        domain = [('mitra_id', '=', mitra_id)] if mitra_id else []
        return [{'id': a.id, 'name': '%s%s' % (a.name, ' — %s' % a.nomor_anggota if a.nomor_anggota else ''),
                 'mitra': a.mitra_id.name}
                for a in self.env['koperasi.anggota'].search(domain, order='name', limit=1000)]

    # ------------------------------------------------------------------
    # Titik masuk
    # ------------------------------------------------------------------
    @api.model
    def get_laporan(self, jenis='ringkasan', mitra_id=False, dari=False, sampai=False, anggota_id=False,
                    pinjaman_id=False, shu_id=False):
        if jenis not in {j for j, _n, _i in JENIS} | JENIS_DOKUMEN:
            jenis = 'ringkasan'
        if jenis in JENIS_PEMBUKUAN and not self.env.user.has_group('koperasi.group_koperasi_operator'):
            jenis = 'ringkasan'
        ctx = self._konteks(jenis, mitra_id, dari, sampai, anggota_id, pinjaman_id)
        ctx['shu'] = self.env['koperasi.shu'].search([('id', '=', shu_id)]) if jenis == 'shu' and shu_id \
            else self.env['koperasi.shu']
        laporan = getattr(self, '_laporan_' + jenis)(ctx)
        zona = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        laporan.update({
            'jenis': jenis,
            'dibuat': zona.strftime('%d/%m/%Y %H:%M'),
            'oleh': self.env.user.name,
            'perusahaan': self.env.company.name,
            'penandatangan': self._penandatangan(),
        })
        # laporan boleh menentukan labelnya sendiri (mis. proyeksi: rentang ke depan)
        laporan.setdefault('mitra_label', ctx['mitra_label'])
        laporan.setdefault('periode_label', ctx['periode_label'])
        laporan.setdefault('perlu_anggota', False)
        # Final bila seluruh periode laporan sudah ditutup; hanya untuk laporan berentang periode
        # (laporan posisi seperti Neraca menentukannya sendiri)
        if 'status_periode' not in laporan:
            laporan['status_periode'] = None
            if jenis not in SATU_PERIODE and jenis not in JENIS_DOKUMEN and not laporan['perlu_anggota']:
                laporan['status_periode'] = self._status_periode(
                    ctx['periode_ids'].filtered(lambda p: p.state != 'ditutup'))
        return laporan

    def _penandatangan(self):
        """Tiga kolom tanda tangan PDF: pembuat laporan dan dua pejabat dari Pengaturan."""
        cfg = self.env['koperasi.pengaturan']._get()
        return [
            {'peran': _('Dibuat oleh,'), 'jabatan': '', 'nama': self.env.user.name},
            {'peran': _('Mengetahui,'), 'jabatan': cfg.ttd_mengetahui_jabatan or '', 'nama': cfg.ttd_mengetahui_nama or ''},
            {'peran': _('Disetujui,'), 'jabatan': cfg.ttd_menyetujui_jabatan or '', 'nama': cfg.ttd_menyetujui_nama or ''},
        ]

    def _status_periode(self, terbuka):
        """terbuka: periode cakupan laporan yang belum ditutup, urut tanggal."""
        return {
            'final': not terbuka,
            'label': _('Final') if not terbuka else _('Sementara'),
            'keterangan': _('Semua periode laporan ini sudah ditutup.') if not terbuka else _(
                '%s belum ditutup; angkanya masih bisa berubah.',
                terbuka[0].name if len(terbuka) == 1 else _('%(a)s – %(b)s', a=terbuka[0].name, b=terbuka[-1].name)),
        }

    def _konteks(self, jenis, mitra_id, dari, sampai, anggota_id, pinjaman_id=False):
        Periode = self.env['koperasi.periode']
        # _untuk_tanggal melepas sudo; pertahankan bila mesin dipanggil dengan sudo (portal anggota)
        sekarang = Periode._untuk_tanggal(fields.Date.context_today(self)).sudo(self.env.su)
        semua_mitra = self.env['koperasi.mitra'].search([])
        mitra = semua_mitra.filtered(lambda m: m.id == mitra_id) if mitra_id else semua_mitra
        anggota = self.env['koperasi.anggota']
        if jenis == 'anggota' and anggota_id:
            # search (bukan browse): aturan akses ikut berlaku, anggota mitra lain dianggap tidak ada
            anggota = anggota.search([('id', '=', anggota_id)])
            mitra = anggota.mitra_id or mitra
        pinjaman = self.env['koperasi.pinjaman']
        if jenis == 'kartu' and pinjaman_id:
            pinjaman = pinjaman.search([('id', '=', pinjaman_id)])
            mitra = pinjaman.mitra_id or mitra
        p_sampai = (Periode.browse(sampai).exists() if sampai else sekarang) or sekarang
        if jenis in SATU_PERIODE:
            dari = p_sampai.id  # satu periode acuan saja
        if jenis == 'anggota' and anggota and not dari:
            awal = self.env['koperasi.tagihan.line']._read_group(
                [('anggota_id', '=', anggota.id)], [], ['periode:min'])[0][0]
            p_dari = Periode._untuk_tanggal(awal).sudo(self.env.su) if awal else p_sampai
        elif dari:
            p_dari = Periode.browse(dari).exists()
        else:
            p_dari = Periode.search([('tahun', '=', p_sampai.tahun), ('bulan', '=', '1')], limit=1) or p_sampai
        if p_dari.tanggal_mulai > p_sampai.tanggal_mulai:
            p_dari, p_sampai = p_sampai, p_dari
        sama = p_dari == p_sampai
        return {
            'mitra': mitra,
            # tabel per mitra hanya berarti bila pengguna melihat lebih dari satu mitra dan belum memilih satu
            'per_mitra': len(semua_mitra) > 1 and not mitra_id and not anggota and not pinjaman,
            'mitra_dipilih': bool(mitra_id),
            'anggota': anggota,
            'pinjaman': pinjaman,
            'p_dari': p_dari, 'p_sampai': p_sampai,
            'ta': p_dari.tanggal_mulai, 'tb': p_sampai.tanggal_akhir,
            'sebelum': p_dari.tanggal_mulai - timedelta(days=1),
            'dm': [('mitra_id', 'in', mitra.ids)],
            'mitra_label': mitra.name if len(mitra) == 1 else _('Semua Mitra'),
            'periode_label': p_dari.name if sama else '%s – %s' % (p_dari.name, p_sampai.name),
            'periode_ids': Periode.search(
                [('tanggal_mulai', '>=', p_dari.tanggal_mulai), ('tanggal_mulai', '<=', p_sampai.tanggal_mulai)],
                order='tanggal_mulai'),
        }

    # ------------------------------------------------------------------
    # Bahan hitung bersama
    # ------------------------------------------------------------------
    def _kelompok(self, model, domain, kunci, agregat):
        """{id: [nilai...]} dari _read_group satu dimensi (None -> 0)."""
        hasil = {}
        for baris in self.env[model]._read_group(domain, [kunci], agregat):
            rec, *nilai = baris
            hasil[rec.id if rec else False] = [v or 0 for v in nilai]
        return hasil

    def _jumlah(self, model, domain, agregat):
        return [v or 0 for v in self.env[model]._read_group(domain, [], agregat)[0]]

    def _baris_tagihan(self, ctx, sejak=None, sampai=None, diterima=False):
        """Rincian tagihan (bukan Draft) per tagihan, status bayar, dan periode pengakuan.

        diterima=False: yang DITAGIHKAN pada rentang (menurut periode tagihan), semua status.
            `tepat` = dibayar pada periodenya sendiri; yang dibayar belakangan tetap dihitung
            belum dibayar pada periode tagihannya.
        diterima=True: yang DITERIMA pada rentang (menurut periode pengakuan); `periode` adalah
            periode pengakuannya dan `susulan` menandai tunggakan periode lalu."""
        Line = self.env['koperasi.tagihan.line']
        domain = ctx['dm'] + [('tagihan_id.state', '!=', 'draft')]
        kunci = 'tanggal_akui' if diterima else 'tagihan_id.periode'
        if sejak:
            domain.append((kunci, '>=', sejak))
        if sampai:
            domain.append((kunci, '<=', sampai))
        if diterima:
            domain.append(('status', '=', 'dibayar'))
        komponen = ('angsuran_pokok', 'angsuran_jasa')
        hasil = []
        for tagihan, st, akui, *nilai in Line._read_group(
                domain, ['tagihan_id', 'status', 'periode_akui_id'], ['%s:sum' % k for k in komponen] + ['__count']):
            v = dict(zip(komponen + ('n',), [x or 0 for x in nilai]))
            tepat = st == 'dibayar' and akui == tagihan.periode_id
            v.update(tagihan=tagihan, periode=akui if diterima else tagihan.periode_id, mitra=tagihan.mitra_id,
                     status=st, tepat=tepat, susulan=st == 'dibayar' and not tepat)
            v['asli'] = v['angsuran_pokok'] + v['angsuran_jasa']
            hasil.append(v)
        return hasil

    def _tunggakan_per_anggota(self, ctx):
        """Definisi sama dengan mitra/anggota/dashboard: baris Belum Dibayar yang belum ditagih
        ulang (utuh) + bagian tunggakan pada baris yang belum divalidasi."""
        Line = self.env['koperasi.tagihan.line']
        hasil = defaultdict(float)
        for a, t in Line._read_group(
                ctx['dm'] + [('status', '=', 'belum'), ('dibawa_ke_id', '=', False)],
                ['anggota_id'], ['total:sum']):
            hasil[a] += t or 0
        for a, t in Line._read_group(
                ctx['dm'] + [('status', '=', 'menunggu'), ('tunggakan', '>', 0)],
                ['anggota_id'], ['tunggakan:sum']):
            hasil[a] += t or 0
        return hasil

    def _pinjaman_rinci(self, ctx):
        """Pinjaman yang sudah cair sampai akhir rentang, lengkap dengan pembayaran angsurannya.

        Per pinjaman: pokok, kembali (pokok dibayar) sampai akhir rentang dan di dalam rentang,
        jasa dibayar di dalam rentang. Piutang awal = pokok - (kembali_sd - kembali_rentang)
        untuk pinjaman yang cair sebelum rentang; piutang akhir = pokok - kembali_sd."""
        Pinjaman = self.env['koperasi.pinjaman']
        Angsuran = self.env['koperasi.pinjaman.angsuran']
        pinjaman = Pinjaman.search(
            ctx['dm'] + [('state', 'in', ('berjalan', 'lunas')), ('tanggal_cair', '<=', ctx['tb'])],
            order='tanggal_cair, id')
        dasar = [('pinjaman_id', 'in', pinjaman.ids), ('status', '=', 'dibayar')]
        sd = self._kelompok('koperasi.pinjaman.angsuran', dasar + [('tanggal_akui', '<=', ctx['tb'])],
                            'pinjaman_id', ['pokok:sum', 'jasa:sum', '__count'])
        rentang = self._kelompok(
            'koperasi.pinjaman.angsuran',
            dasar + [('tanggal_akui', '>=', ctx['ta']), ('tanggal_akui', '<=', ctx['tb'])],
            'pinjaman_id', ['pokok:sum', 'jasa:sum'])
        hasil = []
        for p in pinjaman:
            kembali_sd, _jasa_sd, n = sd.get(p.id, (0, 0, 0))
            kembali_r, jasa_r = rentang.get(p.id, (0, 0))
            baru = p.tanggal_cair >= ctx['ta']
            hasil.append({
                'rec': p, 'baru': baru, 'pokok': p.pokok, 'admin': p.biaya_admin,
                'kembali_rentang': kembali_r, 'jasa_rentang': jasa_r, 'n_dibayar': n,
                'piutang_awal': 0 if baru else p.pokok - (kembali_sd - kembali_r),
                'piutang_akhir': p.pokok - kembali_sd,
            })
        return hasil

    # ------------------------------------------------------------------
    # 1. Ringkasan keuangan
    # ------------------------------------------------------------------
    def _laporan_ringkasan(self, ctx):
        ta, tb = ctx['ta'], ctx['tb']
        baris = self._baris_tagihan(ctx, sampai=tb, diterima=True)
        pinjaman = self._pinjaman_rinci(ctx)
        cair = sum(r['pokok'] for r in pinjaman if r['baru'])
        admin = sum(r['admin'] for r in pinjaman if r['baru'])
        jumlah_cair = sum(1 for r in pinjaman if r['baru'])
        kembali = sum(r['kembali_rentang'] for r in pinjaman)
        jasa = sum(r['jasa_rentang'] for r in pinjaman)
        piutang_awal = sum(r['piutang_awal'] for r in pinjaman)
        piutang_akhir = sum(r['piutang_akhir'] for r in pinjaman)
        penerimaan = kembali + jasa + admin
        arus = penerimaan - cair
        tunggakan = sum(self._tunggakan_per_anggota(ctx).values())
        tersalurkan = sum(r['pokok'] for r in pinjaman)
        anggota_aktif = self.env['koperasi.anggota'].search_count(ctx['dm'] + [('state', '=', 'aktif')])

        K = [kolom('uraian', 'Uraian'), kolom('jumlah', 'Jumlah', 'rp')]

        def isi(uraian, jumlah=None, gaya=None):
            return {'uraian': uraian, 'jumlah': jumlah, '_gaya': gaya}

        posisi = bagian(_('Posisi pada Akhir Periode (%s)', ctx['p_sampai'].name), K, [
            isi(_('Pinjaman'), None, 'judul'),
            isi(_('Dana pinjaman tersalurkan (kumulatif)'), tersalurkan),
            isi(_('Piutang pinjaman (sisa pokok)'), piutang_akhir, 'total'),
            isi(_('Tagihan'), None, 'judul'),
            isi(_('Tunggakan saat ini'), tunggakan),
        ], total=False)
        arus_dana = bagian(_('Arus Dana Periode (%s)', ctx['periode_label']), K, [
            isi(_('Penerimaan'), None, 'judul'),
            isi(_('Angsuran pokok pinjaman'), kembali),
            isi(_('Jasa pinjaman'), jasa),
            isi(_('Biaya admin pencairan'), admin),
            isi(_('Total penerimaan'), penerimaan, 'total'),
            isi(_('Pengeluaran'), None, 'judul'),
            isi(_('Pencairan pinjaman (%d pinjaman)', jumlah_cair), cair),
            isi(_('Arus dana bersih'), arus, 'total'),
        ], total=False, catatan=_(
            'Biaya admin dipotong dari dana yang dicairkan, jadi dicatat sebagai penerimaan '
            'dan pencairan dicatat sebesar pokok.'))
        pendapatan = bagian(_('Pendapatan Platform'), K, [
            isi(_('Jasa pinjaman yang sudah dibayar'), jasa),
            isi(_('Biaya admin pencairan'), admin),
            isi(_('Total pendapatan platform'), jasa + admin, 'total'),
        ], total=False)

        # Rincian per periode
        per = {p.id: {'periode': p.name, 'kembali': 0, 'jasa': 0, 'admin': 0, 'cair': 0}
               for p in ctx['periode_ids']}
        for p_id, (pokok, js) in self._kelompok(
                'koperasi.pinjaman.angsuran',
                [('pinjaman_id.mitra_id', 'in', ctx['mitra'].ids), ('status', '=', 'dibayar'),
                 ('tanggal_akui', '>=', ta), ('tanggal_akui', '<=', tb)],
                'periode_akui_id', ['pokok:sum', 'jasa:sum']).items():
            if p_id in per:
                per[p_id]['kembali'] += pokok
                per[p_id]['jasa'] += js
        indeks = {(p.tahun, int(p.bulan)): p.id for p in ctx['periode_ids']}
        for r in pinjaman:
            if r['baru']:
                p_id = indeks.get((r['rec'].tanggal_cair.year, r['rec'].tanggal_cair.month))
                if p_id in per:
                    per[p_id]['cair'] += r['pokok']
                    per[p_id]['admin'] += r['admin']
        baris_periode = []
        for v in per.values():
            v['arus'] = v['kembali'] + v['jasa'] + v['admin'] - v['cair']
            baris_periode.append(v)
        per_periode = bagian(_('Arus Dana per Periode'), [
            kolom('periode', 'Periode'),
            kolom('kembali', 'Angsuran Pokok', 'rp', True), kolom('jasa', 'Jasa', 'rp', True),
            kolom('admin', 'Biaya Admin', 'rp', True), kolom('cair', 'Pencairan', 'rp', True),
            kolom('arus', 'Arus Bersih', 'rp', True)], baris_periode)

        bagian_laporan = [posisi, arus_dana, pendapatan, per_periode]
        if ctx['per_mitra']:
            bagian_laporan.append(self._tabel_per_mitra(ctx, pinjaman, baris))
        return {
            'judul': _('Ringkasan Keuangan'),
            'subjudul': _('Posisi dan arus dana koperasi, dihitung dari data operasional'),
            'ringkasan': [
                {'label': _('Piutang Pinjaman'), 'nilai': piutang_akhir, 'tipe': 'rp', 'sub': _('sisa pokok, akhir periode')},
                {'label': _('Pendapatan Platform'), 'nilai': jasa + admin, 'tipe': 'rp', 'sub': _('jasa + biaya admin, periode ini')},
                {'label': _('Arus Dana Bersih'), 'nilai': arus, 'tipe': 'rp', 'sub': _('penerimaan − pencairan, periode ini')},
                {'label': _('Anggota Aktif'), 'nilai': anggota_aktif, 'tipe': 'angka', 'sub': _('saat ini')},
                {'label': _('Tunggakan Saat Ini'), 'nilai': tunggakan, 'tipe': 'rp', 'sub': _('semua periode')},
                {'label': _('Piutang Awal Periode'), 'nilai': piutang_awal, 'tipe': 'rp', 'sub': _('sisa pokok sebelum periode')},
            ],
            'bagian': bagian_laporan,
            'catatan': self._catatan_umum() + [
                _('Piutang akhir = piutang awal + pencairan − angsuran pokok yang dibayar.'
                  ' Awal %(awal)s + pencairan %(cair)s − pokok kembali %(kembali)s = akhir %(akhir)s.',
                  awal=self._rp(piutang_awal), cair=self._rp(cair), kembali=self._rp(kembali),
                  akhir=self._rp(piutang_awal + cair - kembali)),
            ],
        }

    def _tabel_per_mitra(self, ctx, pinjaman, baris_saldo):
        """Ringkasan per mitra: piutang, pendapatan, tunggakan."""
        piutang, pendapatan = defaultdict(float), defaultdict(float)
        for r in pinjaman:
            m = r['rec'].mitra_id.id
            piutang[m] += r['piutang_akhir']
            pendapatan[m] += r['jasa_rentang'] + (r['admin'] if r['baru'] else 0)
        tunggakan = defaultdict(float)
        for a, t in self._tunggakan_per_anggota(ctx).items():
            tunggakan[a.mitra_id.id] += t
        aktif = self._kelompok('koperasi.anggota', ctx['dm'] + [('state', '=', 'aktif')], 'mitra_id', ['__count'])
        return bagian(_('Ringkasan per Mitra'), [
            kolom('mitra', 'Mitra'), kolom('anggota', 'Anggota Aktif', 'angka', True),
            kolom('piutang', 'Piutang Pinjaman', 'rp', True),
            kolom('pendapatan', 'Pendapatan Platform', 'rp', True), kolom('tunggakan', 'Tunggakan', 'rp', True)],
            [{'mitra': m.name, 'anggota': aktif.get(m.id, [0])[0],
              'piutang': piutang[m.id], 'pendapatan': pendapatan[m.id], 'tunggakan': tunggakan[m.id],
              '_buka': buka(m)}
             for m in ctx['mitra']])

    # ------------------------------------------------------------------
    # 2. Laporan pinjaman
    # ------------------------------------------------------------------
    def _laporan_pinjaman(self, ctx):
        pinjaman = self._pinjaman_rinci(ctx)
        cair = sum(r['pokok'] for r in pinjaman if r['baru'])
        jumlah_cair = sum(1 for r in pinjaman if r['baru'])
        admin = sum(r['admin'] for r in pinjaman if r['baru'])
        kembali = sum(r['kembali_rentang'] for r in pinjaman)
        jasa = sum(r['jasa_rentang'] for r in pinjaman)
        awal = sum(r['piutang_awal'] for r in pinjaman)
        akhir = sum(r['piutang_akhir'] for r in pinjaman)
        K = [kolom('uraian', 'Uraian'), kolom('jumlah', 'Jumlah', 'rp')]
        rekon = bagian(_('Rekonsiliasi Piutang Pinjaman'), K, [
            {'uraian': _('Piutang awal periode'), 'jumlah': awal, '_gaya': None},
            {'uraian': _('(+) Pencairan pinjaman (%d pinjaman)', jumlah_cair), 'jumlah': cair, '_gaya': None},
            {'uraian': _('(−) Angsuran pokok yang dibayar'), 'jumlah': -kembali, '_gaya': None},
            {'uraian': _('Piutang akhir periode'), 'jumlah': awal + cair - kembali, '_gaya': 'total'},
        ], total=False, catatan=None if abs(awal + cair - kembali - akhir) < 1 else _(
            'Selisih terhadap data pinjaman: %s', self._rp(akhir - (awal + cair - kembali))))

        per_produk = defaultdict(lambda: {'n': 0, 'cair': 0, 'admin': 0, 'kembali': 0, 'jasa': 0, 'piutang': 0})
        for r in pinjaman:
            v = per_produk[r['rec'].produk_id.name]
            if r['baru']:
                v['n'] += 1
                v['cair'] += r['pokok']
                v['admin'] += r['admin']
            v['kembali'] += r['kembali_rentang']
            v['jasa'] += r['jasa_rentang']
            v['piutang'] += r['piutang_akhir']
        tabel_produk = bagian(_('Pinjaman per Produk'), [
            kolom('produk', 'Produk'), kolom('n', 'Pinjaman Cair', 'angka', True), kolom('cair', 'Pokok Dicairkan', 'rp', True),
            kolom('admin', 'Biaya Admin', 'rp', True), kolom('kembali', 'Pokok Kembali', 'rp', True),
            kolom('jasa', 'Jasa Diterima', 'rp', True), kolom('piutang', 'Piutang Akhir', 'rp', True)],
            [dict(v, produk=nama) for nama, v in sorted(per_produk.items())])

        label_status = dict(STATE_PINJAMAN)
        daftar = [{
            'nomor': r['rec'].name, 'anggota': r['rec'].anggota_id.name, 'mitra': r['rec'].mitra_id.name,
            'produk': r['rec'].produk_id.name, 'pokok': r['pokok'], 'tenor': r['rec'].tenor,
            'cair': fields.Date.to_string(r['rec'].tanggal_cair), 'dibayar': '%d / %d' % (r['n_dibayar'], r['rec'].tenor),
            'angsuran': r['rec'].angsuran_bulan, 'sisa': r['piutang_akhir'],
            'status': label_status[r['rec'].state], '_buka': buka(r['rec']),
        } for r in pinjaman][:BATAS_BARIS_PINJAMAN]
        tabel_daftar = bagian(_('Daftar Pinjaman yang Sudah Cair'), [
            kolom('nomor', 'Nomor', 'kode'), kolom('anggota', 'Anggota'), kolom('mitra', 'Mitra'),
            kolom('produk', 'Produk'), kolom('pokok', 'Pokok', 'rp', True), kolom('tenor', 'Tenor', 'angka'),
            kolom('cair', 'Tgl Cair', 'tanggal'), kolom('dibayar', 'Angsuran Dibayar'),
            kolom('angsuran', 'Angsuran/Bulan', 'rp'), kolom('sisa', 'Sisa Pokok', 'rp', True),
            kolom('status', 'Status', 'status')], daftar,
            kosong=_('Belum ada pinjaman yang cair sampai periode ini.'),
            catatan=_('Sisa pokok dihitung per akhir %s.', ctx['p_sampai'].name) + (
                _(' Ditampilkan %d pinjaman pertama.', BATAS_BARIS_PINJAMAN) if len(pinjaman) > BATAS_BARIS_PINJAMAN else ''))

        menunggu = self.env['koperasi.pinjaman']._read_group(
            ctx['dm'] + [('state', 'in', ('diajukan', 'disetujui'))], ['state'], ['__count', 'pokok:sum'])
        antre = {s: (n, p or 0) for s, n, p in menunggu}
        bagian_laporan = [rekon, tabel_produk, tabel_daftar]
        return {
            'judul': _('Laporan Pinjaman'),
            'subjudul': _('Pencairan, pengembalian, piutang, dan pendapatan dari pinjaman'),
            'ringkasan': [
                {'label': _('Dana Dicairkan'), 'nilai': cair, 'tipe': 'rp', 'sub': _('%d pinjaman, %s', jumlah_cair, ctx['periode_label'])},
                {'label': _('Pokok Kembali'), 'nilai': kembali, 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Piutang Akhir'), 'nilai': akhir, 'tipe': 'rp', 'sub': _('akhir %s', ctx['p_sampai'].name)},
                {'label': _('Pendapatan Platform'), 'nilai': jasa + admin, 'tipe': 'rp',
                 'sub': _('jasa %(jasa)s + admin %(admin)s', jasa=self._rp(jasa), admin=self._rp(admin))},
                {'label': _('Menunggu Persetujuan'), 'nilai': antre.get('diajukan', (0, 0))[0], 'tipe': 'angka',
                 'sub': self._rp(antre.get('diajukan', (0, 0))[1]) + _(' (saat ini)')},
                {'label': _('Menunggu Pencairan'), 'nilai': antre.get('disetujui', (0, 0))[0], 'tipe': 'angka',
                 'sub': self._rp(antre.get('disetujui', (0, 0))[1]) + _(' (saat ini)')},
            ],
            'bagian': bagian_laporan,
            'catatan': self._catatan_umum() + [
                _('Pencairan menurut tanggal pencairan; angsuran menurut periode pembayarannya '
                  '(angsuran menunggak yang dibayar belakangan masuk ke periode saat dibayar).')],
        }

    # ------------------------------------------------------------------
    # 4. Tagihan & kolektibilitas
    # ------------------------------------------------------------------
    def _laporan_tagihan(self, ctx):
        baris = self._baris_tagihan(ctx, sejak=ctx['ta'], sampai=ctx['tb'])
        # tunggakan periode lalu yang dibayar pada rentang ini
        susulan = [b for b in self._baris_tagihan(ctx, sejak=ctx['ta'], sampai=ctx['tb'], diterima=True)
                   if b['susulan']]
        # keadaan pada periode tagihannya: yang dibayar belakangan dihitung belum dibayar
        kolom_status = lambda b: 'diterima' if b['tepat'] else 'menunggu' if b['status'] == 'menunggu' else 'belum'
        jumlah = lambda k: sum(b['asli'] for b in baris if kolom_status(b) == k)
        ditagihkan = sum(b['asli'] for b in baris)
        diterima, belum, menunggu = jumlah('diterima'), jumlah('belum'), jumlah('menunggu')
        total_susulan = sum(b['asli'] for b in susulan)
        kolek = lambda d, b: round(d * 100 / (d + b), 1) if (d + b) else None

        per = {p.id: {'periode': p.name, 'n': 0, 'ditagihkan': 0, 'diterima': 0, 'belum': 0, 'menunggu': 0,
                      'susulan': 0,
                      '_buka': {'model': 'koperasi.tagihan', 'nama': _('Tagihan %s', p.name),
                                'domain': ctx['dm'] + [('periode_id', '=', p.id)]}}
               for p in ctx['periode_ids']}
        tagihan_per = defaultdict(set)
        for b in baris:
            v = per.get(b['periode'].id)
            if not v:
                continue
            tagihan_per[b['periode'].id].add(b['tagihan'].id)
            v['ditagihkan'] += b['asli']
            v[kolom_status(b)] += b['asli']
        for b in susulan:
            if b['periode'].id in per:
                per[b['periode'].id]['susulan'] += b['asli']
        rows = []
        for p_id, v in per.items():
            v['n'] = len(tagihan_per[p_id])
            v['kolek'] = kolek(v['diterima'], v['belum'])
            rows.append(v)
        tabel_periode = bagian(_('Tagihan per Periode'), [
            kolom('periode', 'Periode'), kolom('n', 'Jumlah Tagihan', 'angka', True),
            kolom('ditagihkan', 'Ditagihkan', 'rp', True), kolom('diterima', 'Diterima', 'rp', True),
            kolom('belum', 'Belum Dibayar', 'rp', True), kolom('menunggu', 'Belum Divalidasi', 'rp', True),
            kolom('kolek', 'Kolektibilitas', 'persen'), kolom('susulan', 'Tunggakan Diterima', 'rp', True)],
            rows, catatan=_(
            'Diterima dan Belum Dibayar adalah keadaan pada periode tagihannya; tagihan yang dibayar belakangan '
            'tetap tercatat Belum Dibayar di periode asalnya dan muncul sebagai Tunggakan Diterima pada periode '
            'saat dibayar. Kolektibilitas = diterima ÷ (diterima + belum dibayar), hanya dari baris yang sudah divalidasi.'))

        tunggak = self._tunggakan_per_anggota(ctx)
        bagian_laporan = [tabel_periode]
        if ctx['per_mitra']:
            per_mitra = {m.id: {'mitra': m.name, 'ditagihkan': 0, 'diterima': 0, 'belum': 0, 'menunggu': 0,
                                'susulan': 0, 'tunggakan': 0, '_buka': buka(m)}
                         for m in ctx['mitra']}
            for b in baris:
                v = per_mitra.get(b['mitra'].id)
                if v:
                    v['ditagihkan'] += b['asli']
                    v[kolom_status(b)] += b['asli']
            for b in susulan:
                if b['mitra'].id in per_mitra:
                    per_mitra[b['mitra'].id]['susulan'] += b['asli']
            for a, t in tunggak.items():
                if a.mitra_id.id in per_mitra:
                    per_mitra[a.mitra_id.id]['tunggakan'] += t
            for v in per_mitra.values():
                v['kolek'] = kolek(v['diterima'], v['belum'])
            bagian_laporan.append(bagian(_('Tagihan per Mitra'), [
                kolom('mitra', 'Mitra'), kolom('ditagihkan', 'Ditagihkan', 'rp', True), kolom('diterima', 'Diterima', 'rp', True),
                kolom('belum', 'Belum Dibayar', 'rp', True), kolom('menunggu', 'Belum Divalidasi', 'rp', True),
                kolom('kolek', 'Kolektibilitas', 'persen'), kolom('susulan', 'Tunggakan Diterima', 'rp', True),
                kolom('tunggakan', 'Tunggakan Saat Ini', 'rp', True)],
                per_mitra.values()))
        bagian_laporan.append(bagian(_('Tunggakan Saat Ini per Anggota'), [
            kolom('nomor', 'No. Anggota', 'kode'), kolom('nama', 'Anggota'), kolom('mitra', 'Mitra'),
            kolom('tunggakan', 'Tunggakan', 'rp', True)],
            [{'nomor': a.nomor_anggota or '', 'nama': a.name, 'mitra': a.mitra_id.name, 'tunggakan': t,
              '_buka': buka(a)}
             for a, t in sorted(tunggak.items(), key=lambda x: -x[1]) if t],
            kosong=_('Tidak ada anggota yang menunggak.'),
            catatan=_('Keadaan saat ini, tidak terpengaruh oleh pilihan periode.')))
        return {
            'judul': _('Tagihan & Kolektibilitas'),
            'subjudul': _('Penagihan dan pembayaran angsuran pinjaman'),
            'ringkasan': [
                {'label': _('Ditagihkan'), 'nilai': ditagihkan, 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Diterima'), 'nilai': diterima, 'tipe': 'rp', 'sub': _('dibayar pada periodenya')},
                {'label': _('Belum Dibayar'), 'nilai': belum, 'tipe': 'rp', 'sub': _('+ %s belum divalidasi', self._rp(menunggu))},
                {'label': _('Kolektibilitas'), 'nilai': kolek(diterima, belum), 'tipe': 'persen', 'sub': _('dari yang sudah divalidasi')},
                {'label': _('Tunggakan Diterima'), 'nilai': total_susulan, 'tipe': 'rp', 'sub': _('tagihan periode lalu, dibayar %s', ctx['periode_label'])},
                {'label': _('Tunggakan Saat Ini'), 'nilai': sum(tunggak.values()), 'tipe': 'rp', 'sub': _('semua periode')},
            ],
            'bagian': bagian_laporan,
            'catatan': self._catatan_umum() + [
                _('Hanya tagihan yang sudah ditagihkan (bukan Draft). Jumlah = angsuran pokok + angsuran jasa; tunggakan terbawa tidak dihitung dua kali.')],
        }

    # ------------------------------------------------------------------
    # 4. Riwayat anggota
    # ------------------------------------------------------------------
    def _laporan_anggota(self, ctx):
        a = ctx['anggota']
        if not a:
            return {'judul': _('Riwayat Anggota'), 'subjudul': _('Riwayat tagihan dan pinjaman satu anggota'),
                    'ringkasan': [], 'bagian': [], 'catatan': [], 'perlu_anggota': True}
        ta, tb = ctx['ta'], ctx['tb']
        Line = self.env['koperasi.tagihan.line']
        lines = Line.search([('anggota_id', '=', a.id), ('tagihan_id.state', '!=', 'draft'),
                             ('tagihan_id.periode', '>=', ta), ('tagihan_id.periode', '<=', tb)]
                            ).sorted(lambda l: (l.periode, l.id))
        label = dict(STATUS_BARIS)
        rows, total_angsuran = [], 0
        for l in lines:
            tepat = l.status == 'dibayar' and l.periode_akui_id == l.periode_id
            status = label['belum'] if l.status == 'dibayar' and not tepat else label.get(l.status, l.status)
            if l.status == 'dibayar':
                total_angsuran += l.angsuran_pokok + l.angsuran_jasa
            rows.append({
                'periode': l.periode_id.name, 'angsuran_pokok': l.angsuran_pokok, 'angsuran_jasa': l.angsuran_jasa,
                'tunggakan': l.tunggakan, 'total': l.total, 'status': status, '_buka': buka(l)})
        riwayat = bagian(_('Riwayat Tagihan'), [
            kolom('periode', 'Periode'), kolom('angsuran_pokok', 'Angsuran Pokok', 'rp', True),
            kolom('angsuran_jasa', 'Angsuran Jasa', 'rp', True), kolom('tunggakan', 'Tunggakan Dibawa', 'rp', True),
            kolom('total', 'Jumlah', 'rp', True), kolom('status', 'Status Bayar', 'status')], rows,
            kosong=_('Tidak ada tagihan pada periode ini.'))

        pinjaman = a.pinjaman_ids.filtered(lambda p: p.state in ('berjalan', 'lunas')).sorted('tanggal_cair')
        daftar = [{'nomor': p.name, 'produk': p.produk_id.name, 'pokok': p.pokok, 'tenor': p.tenor,
                   'cair': fields.Date.to_string(p.tanggal_cair), 'angsuran': p.angsuran_bulan,
                   'dibayar': '%d / %d' % (p.angsuran_dibayar, p.tenor), 'sisa': p.sisa_pokok,
                   'status': dict(STATE_PINJAMAN)[p.state], '_buka': buka(p)} for p in pinjaman]
        tabel_pinjaman = bagian(_('Pinjaman'), [
            kolom('nomor', 'Nomor', 'kode'), kolom('produk', 'Produk'), kolom('pokok', 'Pokok', 'rp'),
            kolom('tenor', 'Tenor', 'angka'), kolom('cair', 'Tgl Cair', 'tanggal'),
            kolom('angsuran', 'Angsuran/Bulan', 'rp'), kolom('dibayar', 'Dibayar'),
            kolom('sisa', 'Sisa Pokok', 'rp'), kolom('status', 'Status', 'status')], daftar, total=False,
            kosong=_('Tidak ada pinjaman.'))
        jadwal = []
        for p in pinjaman:
            for x in p.angsuran_ids.sorted('ke'):
                if ta <= x.jatuh_tempo <= tb:
                    jadwal.append({'nomor': p.name, 'ke': x.ke, 'periode': x.periode_id.name, 'pokok': x.pokok,
                                   'jasa': x.jasa, 'total': x.total,
                                   'status': dict(x._fields['status'].selection)[x.status]})
        tabel_angsuran = bagian(_('Angsuran Pinjaman pada Periode Laporan'), [
            kolom('nomor', 'Pinjaman', 'kode'), kolom('ke', 'Angsuran Ke', 'angka'), kolom('periode', 'Periode'),
            kolom('pokok', 'Pokok', 'rp', True), kolom('jasa', 'Jasa', 'rp', True), kolom('total', 'Jumlah', 'rp', True),
            kolom('status', 'Status', 'status')], jadwal, kosong=_('Tidak ada angsuran pada periode ini.'))
        tunggakan = self._tunggakan_per_anggota(dict(ctx, dm=[('anggota_id', '=', a.id)]))
        return {
            'judul': _('Riwayat Anggota'),
            'subjudul': '%s%s' % (a.name, ' — %s' % a.nomor_anggota if a.nomor_anggota else ''),
            'ringkasan': [
                {'label': _('Angsuran Dibayar'), 'nilai': total_angsuran, 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Sisa Pinjaman'), 'nilai': sum(pinjaman.mapped('sisa_pokok')), 'tipe': 'rp', 'sub': _('saat ini')},
                {'label': _('Tunggakan'), 'nilai': sum(tunggakan.values()), 'tipe': 'rp', 'sub': _('saat ini')},
            ],
            'identitas': [
                (_('Nama'), a.name), (_('No. Anggota'), a.nomor_anggota or '-'), (_('NIP / ID Karyawan'), a.nip or '-'),
                (_('Mitra'), a.mitra_id.name), (_('Jabatan'), a.jabatan or '-'),
                (_('Bergabung'), a.tanggal_bergabung.strftime('%d/%m/%Y') if a.tanggal_bergabung else '-')] + (
                [(_('Keluar'), a.tanggal_keluar.strftime('%d/%m/%Y'))] if a.tanggal_keluar else []),
            'bagian': [riwayat, tabel_pinjaman, tabel_angsuran],
            'catatan': self._catatan_umum(),
        }

    # ------------------------------------------------------------------
    # 5. Umur tunggakan & kualitas pinjaman
    # ------------------------------------------------------------------
    def _laporan_kualitas(self, ctx):
        """Tunggakan dikelompokkan menurut lamanya, dan pinjaman menurut kolektibilitasnya.

        Tunggakan = angsuran (pokok + jasa) semua baris tagihan berstatus Belum Dibayar.
        Jumlahnya sama dengan definisi tunggakan di dashboard, karena baris yang sudah ditagih ulang
        tetap berstatus Belum Dibayar sampai baris pembawanya dibayar."""
        acuan = ctx['p_sampai'].tanggal_mulai

        def umur(tgl):
            return (acuan.year - tgl.year) * 12 + acuan.month - tgl.month

        Line = self.env['koperasi.tagihan.line']
        lines = Line.search(ctx['dm'] + [
            ('status', '=', 'belum'), ('tagihan_id.state', '!=', 'draft'), ('tagihan_id.periode', '<=', acuan)])
        kel = [{'umur': label, 'anggota': set(), 'angsuran': 0} for label in KELOMPOK_UMUR]
        per_mitra = {m.id: {'mitra': m.name, 'k0': 0, 'k1': 0, 'k2': 0, 'k3': 0, 'total': 0,
                            'anggota': set(), '_buka': buka(m)} for m in ctx['mitra']}
        per_anggota = {}
        for l in lines:
            u = min(max(umur(l.periode), 0), 3)
            angsuran = l.angsuran_pokok + l.angsuran_jasa
            kel[u]['anggota'].add(l.anggota_id.id)
            kel[u]['angsuran'] += angsuran
            m = per_mitra.get(l.mitra_id.id)
            if m:
                m['k%d' % u] += angsuran
                m['total'] += angsuran
                m['anggota'].add(l.anggota_id.id)
            a = per_anggota.setdefault(l.anggota_id, {'tertua': l.periode, 'periode': l.periode_id.name,
                                                      'n': 0, 'angsuran': 0})
            if l.periode < a['tertua']:
                a['tertua'], a['periode'] = l.periode, l.periode_id.name
            a['n'] += 1
            a['angsuran'] += angsuran
        total = sum(k['angsuran'] for k in kel)
        tabel_umur = bagian(_('Umur Tunggakan'), [
            kolom('umur', 'Lama Menunggak'), kolom('anggota', 'Anggota', 'angka'),
            kolom('angsuran', 'Angsuran Pinjaman', 'rp', True), kolom('persen', 'Porsi', 'persen')],
            [{'umur': k['umur'], 'anggota': len(k['anggota']), 'angsuran': k['angsuran'],
              'persen': round(k['angsuran'] * 100 / total, 1) if total else None} for k in kel],
            catatan=_('Lama menunggak dihitung dari periode tagihan sampai %s. Satu anggota bisa muncul '
                      'di lebih dari satu kelompok.', ctx['p_sampai'].name))
        bagian_laporan = [tabel_umur]
        if ctx['per_mitra']:
            bagian_laporan.append(bagian(_('Umur Tunggakan per Mitra'), [
                kolom('mitra', 'Mitra'), kolom('anggota', 'Anggota Menunggak', 'angka', True),
                kolom('k0', KELOMPOK_UMUR[0], 'rp', True), kolom('k1', KELOMPOK_UMUR[1], 'rp', True),
                kolom('k2', KELOMPOK_UMUR[2], 'rp', True), kolom('k3', KELOMPOK_UMUR[3], 'rp', True),
                kolom('total', 'Jumlah', 'rp', True)],
                [dict(v, anggota=len(v['anggota'])) for v in per_mitra.values()]))
        bagian_laporan.append(bagian(_('Anggota Menunggak'), [
            kolom('nomor', 'No. Anggota', 'kode'), kolom('nama', 'Anggota'), kolom('mitra', 'Mitra'),
            kolom('periode', 'Sejak Periode'), kolom('umur', 'Umur (bulan)', 'angka'),
            kolom('n', 'Tagihan', 'angka'), kolom('angsuran', 'Angsuran Pinjaman', 'rp', True)],
            [{'nomor': a.nomor_anggota or '', 'nama': a.name, 'mitra': a.mitra_id.name, 'periode': v['periode'],
              'umur': max(umur(v['tertua']), 0), 'n': v['n'], 'angsuran': v['angsuran'], '_buka': buka(a)}
             for a, v in sorted(per_anggota.items(), key=lambda x: (x[1]['tertua'], -x[1]['angsuran']))],
            kosong=_('Tidak ada anggota yang menunggak.')))

        # ---- Kualitas pinjaman: menurut umur angsuran tertua yang belum dibayar
        Pinjaman = self.env['koperasi.pinjaman']
        pinjaman = Pinjaman.search(ctx['dm'] + [('state', '=', 'berjalan'), ('tanggal_cair', '<=', ctx['tb'])])
        tunggak = {}
        for x in self.env['koperasi.pinjaman.angsuran'].search([
                ('pinjaman_id', 'in', pinjaman.ids), ('tagihan_line_id.status', '=', 'belum'),
                ('jatuh_tempo', '<=', acuan)]):
            t = tunggak.setdefault(x.pinjaman_id.id, {'n': 0, 'nominal': 0, 'tertua': x.jatuh_tempo,
                                                      'periode': x.periode_id.name})
            t['n'] += 1
            t['nominal'] += x.total
            if x.jatuh_tempo < t['tertua']:
                t['tertua'], t['periode'] = x.jatuh_tempo, x.periode_id.name

        def kategori(p):
            t = tunggak.get(p.id)
            return 0 if not t else min(max(umur(t['tertua']), 0), 3) + 1

        per_kategori = [{'kategori': k, 'n': 0, 'sisa': 0} for k in KATEGORI_PINJAMAN]
        for p in pinjaman:
            per_kategori[kategori(p)]['n'] += 1
            per_kategori[kategori(p)]['sisa'] += p.sisa_pokok
        total_sisa = sum(k['sisa'] for k in per_kategori)
        bermasalah = sum(k['sisa'] for k in per_kategori[2:])
        for k in per_kategori:
            k['persen'] = round(k['sisa'] * 100 / total_sisa, 1) if total_sisa else None
        bagian_laporan.append(bagian(_('Kualitas Pinjaman Berjalan'), [
            kolom('kategori', 'Kategori', 'status'), kolom('n', 'Jumlah Pinjaman', 'angka', True),
            kolom('sisa', 'Sisa Pokok', 'rp', True), kolom('persen', 'Porsi', 'persen')], per_kategori,
            catatan=_('Menurut angsuran tertua yang belum dibayar: Dalam Perhatian = bulan berjalan, '
                      'Kurang Lancar = 1 bulan, Diragukan = 2 bulan, Macet = 3 bulan atau lebih. '
                      'Pinjaman bermasalah = Kurang Lancar + Diragukan + Macet.')))
        bagian_laporan.append(bagian(_('Pinjaman dengan Angsuran Menunggak'), [
            kolom('nomor', 'Nomor', 'kode'), kolom('anggota', 'Anggota'), kolom('mitra', 'Mitra'),
            kolom('periode', 'Sejak Periode'), kolom('n', 'Angsuran Menunggak', 'angka'),
            kolom('nominal', 'Nilai Menunggak', 'rp', True), kolom('sisa', 'Sisa Pokok', 'rp', True),
            kolom('kategori', 'Kategori', 'status')],
            [{'nomor': p.name, 'anggota': p.anggota_id.name, 'mitra': p.mitra_id.name,
              'periode': tunggak[p.id]['periode'], 'n': tunggak[p.id]['n'], 'nominal': tunggak[p.id]['nominal'],
              'sisa': p.sisa_pokok, 'kategori': KATEGORI_PINJAMAN[kategori(p)], '_buka': buka(p)}
             for p in pinjaman.sorted(lambda p: -kategori(p)) if p.id in tunggak],
            kosong=_('Semua pinjaman berjalan lancar.')))
        return {
            'judul': _('Umur Tunggakan & Kualitas Pinjaman'),
            'subjudul': _('Seberapa lama tagihan menunggak dan seberapa sehat pinjaman yang berjalan'),
            'ringkasan': [
                {'label': _('Total Tunggakan'), 'nilai': total, 'tipe': 'rp', 'sub': _('per %s', ctx['p_sampai'].name)},
                {'label': _('Anggota Menunggak'), 'nilai': len(per_anggota), 'tipe': 'angka', 'sub': _('orang')},
                {'label': _('Menunggak 3 Bulan+'), 'nilai': kel[3]['angsuran'], 'tipe': 'rp',
                 'sub': _('%d anggota', len(kel[3]['anggota']))},
                {'label': _('Pinjaman Bermasalah'), 'nilai': round(bermasalah * 100 / total_sisa, 1) if total_sisa else None,
                 'tipe': 'persen', 'sub': _('%s dari sisa pokok', self._rp(bermasalah)) if total_sisa else _('belum ada pinjaman berjalan')},
                {'label': _('Sisa Pokok Berjalan'), 'nilai': total_sisa, 'tipe': 'rp', 'sub': _('%d pinjaman', len(pinjaman))},
            ],
            'bagian': bagian_laporan,
            'catatan': self._catatan_umum() + [
                _('Keadaan saat ini (status pembayaran terbaru), dengan umur dihitung terhadap periode acuan.')],
        }

    # ------------------------------------------------------------------
    # 6. Proyeksi penerimaan
    # ------------------------------------------------------------------
    def _laporan_proyeksi(self, ctx):
        """Penerimaan yang diharapkan pada bulan-bulan mendatang, dari jadwal angsuran pinjaman."""
        mulai = ctx['p_sampai'].tanggal_mulai + relativedelta(months=1)
        bulan = [mulai + relativedelta(months=i) for i in range(BULAN_PROYEKSI)]
        akhir = bulan[-1] + relativedelta(months=1, days=-1)
        nama = lambda d: '%s %s' % (BULAN_SINGKAT[str(d.month)], d.year)
        ids = ctx['mitra'].ids

        # Angsuran yang belum ditagihkan; yang jatuh temponya sudah lewat masuk ke bulan pertama
        dasar = [('mitra_id', 'in', ids), ('pinjaman_id.state', '=', 'berjalan'),
                 ('status', '=', 'terjadwal'), ('jatuh_tempo', '<=', akhir)]
        per = {(d.year, d.month): {'periode': nama(d), 'pokok': 0, 'jasa': 0} for d in bulan}
        for tgl, pokok, jasa in self.env['koperasi.pinjaman.angsuran']._read_group(
                dasar, ['jatuh_tempo:month'], ['pokok:sum', 'jasa:sum']):
            kunci = (tgl.year, tgl.month) if tgl >= mulai else (mulai.year, mulai.month)
            per[kunci]['pokok'] += pokok or 0
            per[kunci]['jasa'] += jasa or 0

        berjalan = self._kelompok('koperasi.pinjaman', ctx['dm'] + [('state', '=', 'berjalan')],
                                  'mitra_id', ['__count', 'sisa_pokok:sum'])
        piutang = sum(v[1] for v in berjalan.values())
        sisa, rows = piutang, []
        for v in per.values():
            v['total'] = v['pokok'] + v['jasa']
            sisa -= v['pokok']
            v['piutang'] = sisa
            rows.append(v)
        total = sum(v['total'] for v in rows)
        total_jasa = sum(v['jasa'] for v in rows)
        total_pokok = sum(v['pokok'] for v in rows)
        tabel = bagian(_('Proyeksi per Bulan'), [
            kolom('periode', 'Periode'), kolom('pokok', 'Angsuran Pokok', 'rp', True), kolom('jasa', 'Jasa', 'rp', True),
            kolom('total', 'Total Penerimaan', 'rp', True), kolom('piutang', 'Sisa Piutang', 'rp')], rows,
            catatan=_('Sisa piutang = sisa pokok saat ini dikurangi angsuran pokok yang dijadwalkan.'))
        bagian_laporan = [tabel]

        if ctx['per_mitra']:
            ang_mitra = self._kelompok('koperasi.pinjaman.angsuran', dasar, 'mitra_id', ['pokok:sum', 'jasa:sum'])
            Anggota = self.env['koperasi.anggota']
            aktif = self._kelompok('koperasi.anggota', ctx['dm'] + [('state', '=', 'aktif')], 'mitra_id', ['__count'])
            rows_mitra = []
            for m in ctx['mitra']:
                pokok, jasa = ang_mitra.get(m.id, (0, 0))
                rows_mitra.append({
                    'mitra': m.name, 'anggota': aktif.get(m.id, [0])[0], 'pinjaman': berjalan.get(m.id, (0, 0))[0],
                    'pokok': pokok, 'jasa': jasa, 'total': pokok + jasa, '_buka': buka(m)})
            bagian_laporan.append(bagian(_('Proyeksi per Mitra (%d bulan)', BULAN_PROYEKSI), [
                kolom('mitra', 'Mitra'), kolom('anggota', 'Anggota Aktif', 'angka', True),
                kolom('pinjaman', 'Pinjaman Berjalan', 'angka', True), kolom('pokok', 'Angsuran Pokok', 'rp', True),
                kolom('jasa', 'Jasa', 'rp', True), kolom('total', 'Total Penerimaan', 'rp', True)], rows_mitra))

        menunggu = sum(self._jumlah('koperasi.tagihan.line', ctx['dm'] + [
            ('status', '=', 'menunggu'), ('tagihan_id.state', '!=', 'draft')],
            ['angsuran_pokok:sum', 'angsuran_jasa:sum']))
        tunggakan = sum(self._tunggakan_per_anggota(ctx).values())
        return {
            'judul': _('Proyeksi Penerimaan'),
            'subjudul': _('Penerimaan yang diharapkan %d bulan ke depan dari angsuran pinjaman terjadwal',
                          BULAN_PROYEKSI),
            'periode_label': '%s – %s' % (nama(bulan[0]), nama(bulan[-1])),
            'ringkasan': [
                {'label': _('Total Proyeksi'), 'nilai': total, 'tipe': 'rp', 'sub': _('%d bulan ke depan', BULAN_PROYEKSI)},
                {'label': _('Rata-rata per Bulan'), 'nilai': total / BULAN_PROYEKSI, 'tipe': 'rp', 'sub': _('seluruh penerimaan')},
                {'label': _('Angsuran Pokok Kembali'), 'nilai': total_pokok, 'tipe': 'rp',
                 'sub': _('dari piutang %s', self._rp(piutang))},
                {'label': _('Jasa Diproyeksikan'), 'nilai': total_jasa, 'tipe': 'rp', 'sub': _('pendapatan platform')},
                {'label': _('Belum Diterima Saat Ini'), 'nilai': menunggu + tunggakan, 'tipe': 'rp',
                 'sub': _('belum divalidasi %(a)s + tunggakan %(b)s', a=self._rp(menunggu), b=self._rp(tunggakan))},
            ],
            'bagian': bagian_laporan,
            'catatan': self._catatan_umum() + [
                _('Asumsi: semua tagihan dibayar tepat waktu dan tidak ada pinjaman baru.'),
                _('Angsuran dihitung dari jadwal pinjaman berjalan yang belum ditagihkan. Tagihan yang sudah '
                  'ditagihkan tetapi belum dibayar tidak termasuk; lihat "Belum Diterima Saat Ini".')],
        }

    # ------------------------------------------------------------------
    # 7. Kartu pinjaman (per dokumen, dibuka dari form pinjaman)
    # ------------------------------------------------------------------
    def _laporan_kartu(self, ctx):
        p = ctx['pinjaman']
        if not p:
            return {'judul': _('Kartu Pinjaman'), 'subjudul': '', 'ringkasan': [], 'bagian': [], 'catatan': [],
                    'perlu_anggota': True}
        a = p.anggota_id
        tgl = lambda d: d.strftime('%d/%m/%Y') if d else '-'
        label_status = dict(STATE_PINJAMAN)
        rows, sisa = [], p.pokok
        # sudo: status dan nomor tagihan angsuran milik pinjaman ini tetap ditampilkan walau tagihannya
        # belum terlihat oleh admin mitra (masih Draft); tidak ada data pihak lain yang ikut terbaca.
        for x in p.sudo().angsuran_ids.sorted('ke'):
            sisa -= x.pokok
            line = x.tagihan_line_id
            if line.status == 'belum':
                status = _('Belum Dibayar')
            else:
                status = dict(x._fields['status'].selection)[x.status]
            rows.append({'ke': x.ke, 'periode': x.periode_id.name, 'pokok': x.pokok, 'jasa': x.jasa, 'total': x.total,
                         'sisa': sisa, 'tagihan': line.tagihan_id.name or '', 'status': status,
                         'dibayar': fields.Date.to_string(line.tanggal_bayar) if x.status == 'dibayar' and line.tanggal_bayar else None})
        if not rows:
            # belum cair: tampilkan rencana angsuran tanpa periode
            for ke in range(1, p.tenor + 1):
                pokok = sisa if ke == p.tenor else min(p.pokok_bulan, sisa)
                sisa -= pokok
                rows.append({'ke': ke, 'periode': _('Bulan ke-%d setelah cair', ke), 'pokok': pokok, 'jasa': p.jasa_bulan,
                             'total': pokok + p.jasa_bulan, 'sisa': sisa, 'tagihan': '', 'status': _('Rencana'), 'dibayar': None})
        jadwal = bagian(_('Jadwal Angsuran'), [
            kolom('ke', 'Ke', 'angka'), kolom('periode', 'Periode'), kolom('pokok', 'Pokok', 'rp', True),
            kolom('jasa', 'Jasa', 'rp', True), kolom('total', 'Jumlah Angsuran', 'rp', True),
            kolom('sisa', 'Sisa Sesuai Jadwal', 'rp'), kolom('tagihan', 'Tagihan', 'kode'),
            kolom('status', 'Status', 'status'), kolom('dibayar', 'Tgl Dibayar', 'tanggal')], rows,
            catatan=None if p.angsuran_ids else _('Pinjaman belum dicairkan: ini rencana angsuran, periode ditetapkan saat pencairan.'))
        if p.admin_mode == 'nominal':
            admin = self._rp(p.biaya_admin)
        else:
            admin = '%s (%s%%)' % (self._rp(p.biaya_admin), ('%.2f' % p.admin_persen).rstrip('0').rstrip('.').replace('.', ','))
        rekening = '%s %s a.n. %s' % (p.rekening_bank, p.rekening_nomor, p.rekening_atas_nama) if p.rekening_nomor else '-'
        return {
            'judul': _('Kartu Pinjaman'),
            'subjudul': '%s — %s' % (p.name, a.name),
            'nama_berkas': 'Kartu Pinjaman %s' % p.name,
            'periode_label': '%s – %s' % (rows[0]['periode'], rows[-1]['periode']) if p.angsuran_ids else _('Belum dicairkan'),
            'identitas': [
                (_('Nomor Pinjaman'), p.name), (_('Anggota'), a.name), (_('No. Anggota'), a.nomor_anggota or '-'),
                (_('NIP / ID Karyawan'), a.nip or '-'), (_('Produk'), p.produk_id.name),
                (_('Jasa per Bulan'), ('%.2f' % p.jasa_persen).rstrip('0').rstrip('.').replace('.', ',') + '%'),
                (_('Biaya Admin'), admin), (_('Keperluan'), p.keperluan or '-'),
                (_('Diajukan'), tgl(p.tanggal_ajukan)), (_('Disetujui'), tgl(p.tanggal_setuju)),
                (_('Dicairkan'), tgl(p.tanggal_cair)), (_('Rekening Pencairan'), rekening)],
            'ringkasan': [
                {'label': _('Pokok Pinjaman'), 'nilai': p.pokok, 'tipe': 'rp', 'sub': _('tenor %d bulan', p.tenor)},
                {'label': _('Diterima Anggota'), 'nilai': p.diterima, 'tipe': 'rp', 'sub': _('setelah biaya admin')},
                {'label': _('Angsuran per Bulan'), 'nilai': p.angsuran_bulan, 'tipe': 'rp',
                 'sub': _('pokok %(a)s + jasa %(b)s', a=self._rp(p.pokok_bulan), b=self._rp(p.jasa_bulan))},
                {'label': _('Total Pembayaran'), 'nilai': p.total_bayar, 'tipe': 'rp', 'sub': _('jasa %s', self._rp(p.total_jasa))},
                {'label': _('Sisa Pokok'), 'nilai': p.sisa_pokok, 'tipe': 'rp',
                 'sub': _('%(n)d dari %(t)d angsuran dibayar', n=p.angsuran_dibayar, t=p.tenor)},
                {'label': _('Status'), 'nilai': None, 'tipe': 'teks', 'sub': label_status[p.state]},
            ],
            'bagian': [jadwal],
            'catatan': [_('Status angsuran mengikuti validasi pembayaran pada tagihan mitra.')],
        }

    # ------------------------------------------------------------------
    def _rp(self, nilai):
        return 'Rp %s' % '{:,.0f}'.format(nilai or 0).replace(',', '.')

    def _catatan_umum(self):
        return [_('Laporan manajemen yang dihitung dari data operasional koperasi, bukan dari jurnal pembukuan. '
                  'Laporan keuangan (Neraca, Laba Rugi, Arus Kas) disusun terpisah dari jurnal.')]
