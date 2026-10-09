"""Mesin laporan koperasi.

Satu struktur data standar dipakai tiga penampil: halaman di layar (OWL), PDF (QWeb)
dan Excel (xlsxwriter). Semua angka dihitung dari data operasional dengan hak pengguna
yang memanggil, tanpa sudo: admin mitra otomatis hanya mendapat mitranya, dan tagihan
Draft tidak ikut terhitung baginya.

Ini laporan manajemen turunan data operasional, BUKAN Neraca / Laba Rugi akuntansi:
aplikasi belum memiliki kas/bank, biaya operasional, dan sumber dana.
"""
from collections import defaultdict
from datetime import timedelta

from odoo import api, fields, models, _

from .koperasi_periode import BULAN_SINGKAT
from .koperasi_pinjaman import STATE_PINJAMAN
from .koperasi_tagihan import STATE_TAGIHAN, STATUS_BARIS

JENIS = [
    ('ringkasan', 'Ringkasan Keuangan', 'fa-line-chart'),
    ('simpanan', 'Laporan Simpanan', 'fa-bank'),
    ('pinjaman', 'Laporan Pinjaman', 'fa-credit-card'),
    ('tagihan', 'Tagihan & Kolektibilitas', 'fa-file-text-o'),
    ('anggota', 'Buku Simpanan Anggota', 'fa-address-book-o'),
]
BATAS_BARIS_PINJAMAN = 500


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
        return {
            'jenis': [{'id': j, 'label': n, 'ikon': i} for j, n, i in JENIS],
            'is_operator': self.env.user.has_group('koperasi.group_koperasi_operator'),
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
    def get_laporan(self, jenis='ringkasan', mitra_id=False, dari=False, sampai=False, anggota_id=False):
        if jenis not in {j for j, _n, _i in JENIS}:
            jenis = 'ringkasan'
        ctx = self._konteks(jenis, mitra_id, dari, sampai, anggota_id)
        laporan = getattr(self, '_laporan_' + jenis)(ctx)
        zona = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        laporan.update({
            'jenis': jenis,
            'mitra_label': ctx['mitra_label'],
            'periode_label': ctx['periode_label'],
            'dibuat': zona.strftime('%d/%m/%Y %H:%M'),
            'oleh': self.env.user.name,
            'perusahaan': self.env.company.name,
        })
        laporan.setdefault('perlu_anggota', False)
        return laporan

    def _konteks(self, jenis, mitra_id, dari, sampai, anggota_id):
        Periode = self.env['koperasi.periode']
        sekarang = Periode._untuk_tanggal(fields.Date.context_today(self))
        semua_mitra = self.env['koperasi.mitra'].search([])
        mitra = semua_mitra.filtered(lambda m: m.id == mitra_id) if mitra_id else semua_mitra
        anggota = self.env['koperasi.anggota']
        if jenis == 'anggota' and anggota_id:
            # search (bukan browse): aturan akses ikut berlaku, anggota mitra lain dianggap tidak ada
            anggota = anggota.search([('id', '=', anggota_id)])
            mitra = anggota.mitra_id or mitra
        p_sampai = Periode.browse(sampai).exists() if sampai else sekarang
        if jenis == 'anggota' and anggota and not dari:
            awal = self.env['koperasi.tagihan.line']._read_group(
                [('anggota_id', '=', anggota.id)], [], ['periode:min'])[0][0]
            p_dari = Periode._untuk_tanggal(awal) if awal else p_sampai
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
            'per_mitra': len(semua_mitra) > 1 and not mitra_id and not anggota,
            'anggota': anggota,
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

    def _baris_tagihan(self, ctx, sejak=None, sampai=None, status=None):
        """Rincian tagihan (bukan Draft) per tagihan dan status bayar pada rentang tanggal periode."""
        Line = self.env['koperasi.tagihan.line']
        domain = ctx['dm'] + [('tagihan_id.state', '!=', 'draft')]
        if sejak:
            domain.append(('tagihan_id.periode', '>=', sejak))
        if sampai:
            domain.append(('tagihan_id.periode', '<=', sampai))
        if status:
            domain.append(('status', '=', status))
        komponen = ('simpok', 'simwa', 'simsula', 'angsuran_pokok', 'angsuran_jasa')
        hasil = []
        for tagihan, st, *nilai in Line._read_group(
                domain, ['tagihan_id', 'status'], ['%s:sum' % k for k in komponen] + ['__count']):
            v = dict(zip(komponen + ('n',), [x or 0 for x in nilai]))
            v.update(tagihan=tagihan, periode=tagihan.periode_id, mitra=tagihan.mitra_id, status=st)
            v['simpanan'] = v['simpok'] + v['simwa'] + v['simsula']
            v['angsuran'] = v['angsuran_pokok'] + v['angsuran_jasa']
            v['asli'] = v['simpanan'] + v['angsuran']
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
        sd = self._kelompok('koperasi.pinjaman.angsuran', dasar + [('jatuh_tempo', '<=', ctx['tb'])],
                            'pinjaman_id', ['pokok:sum', 'jasa:sum', '__count'])
        rentang = self._kelompok(
            'koperasi.pinjaman.angsuran',
            dasar + [('jatuh_tempo', '>=', ctx['ta']), ('jatuh_tempo', '<=', ctx['tb'])],
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
        baris = self._baris_tagihan(ctx, sampai=tb, status='dibayar')
        masuk = [b for b in baris if b['periode'].tanggal_mulai >= ta]
        saldo_akhir = {k: sum(b[k] for b in baris) for k in ('simpok', 'simwa', 'simsula')}
        diterima = {k: sum(b[k] for b in masuk) for k in ('simpok', 'simwa', 'simsula', 'angsuran_pokok', 'angsuran_jasa')}
        pinjaman = self._pinjaman_rinci(ctx)
        cair = sum(r['pokok'] for r in pinjaman if r['baru'])
        admin = sum(r['admin'] for r in pinjaman if r['baru'])
        jumlah_cair = sum(1 for r in pinjaman if r['baru'])
        kembali = sum(r['kembali_rentang'] for r in pinjaman)
        jasa = sum(r['jasa_rentang'] for r in pinjaman)
        piutang_awal = sum(r['piutang_awal'] for r in pinjaman)
        piutang_akhir = sum(r['piutang_akhir'] for r in pinjaman)
        simpanan_masuk = diterima['simpok'] + diterima['simwa'] + diterima['simsula']
        penerimaan = simpanan_masuk + kembali + jasa + admin
        arus = penerimaan - cair
        total_simpanan = sum(saldo_akhir.values())
        tunggakan = sum(self._tunggakan_per_anggota(ctx).values())
        tersalurkan = sum(r['pokok'] for r in pinjaman)
        anggota_aktif = self.env['koperasi.anggota'].search_count(ctx['dm'] + [('state', '=', 'aktif')])

        K = [kolom('uraian', 'Uraian'), kolom('jumlah', 'Jumlah', 'rp')]

        def isi(uraian, jumlah=None, gaya=None):
            return {'uraian': uraian, 'jumlah': jumlah, '_gaya': gaya}

        posisi = bagian(_('Posisi pada Akhir Periode (%s)', ctx['p_sampai'].name), K, [
            isi(_('Simpanan anggota'), None, 'judul'),
            isi(_('Simpanan pokok'), saldo_akhir['simpok']),
            isi(_('Simpanan wajib'), saldo_akhir['simwa']),
            isi(_('Simpanan sukarela'), saldo_akhir['simsula']),
            isi(_('Total simpanan anggota'), total_simpanan, 'total'),
            isi(_('Pinjaman'), None, 'judul'),
            isi(_('Dana pinjaman tersalurkan (kumulatif)'), tersalurkan),
            isi(_('Piutang pinjaman (sisa pokok)'), piutang_akhir, 'total'),
            isi(_('Tagihan'), None, 'judul'),
            isi(_('Tunggakan saat ini'), tunggakan),
        ], total=False)
        arus_dana = bagian(_('Arus Dana Periode (%s)', ctx['periode_label']), K, [
            isi(_('Penerimaan'), None, 'judul'),
            isi(_('Simpanan pokok'), diterima['simpok']),
            isi(_('Simpanan wajib'), diterima['simwa']),
            isi(_('Simpanan sukarela'), diterima['simsula']),
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
        per = {p.id: {'periode': p.name, 'simpanan': 0, 'kembali': 0, 'jasa': 0, 'admin': 0, 'cair': 0}
               for p in ctx['periode_ids']}
        for b in masuk:
            if b['periode'].id in per:
                per[b['periode'].id]['simpanan'] += b['simpanan']
        for p_id, (pokok, js) in self._kelompok(
                'koperasi.pinjaman.angsuran',
                [('pinjaman_id.mitra_id', 'in', ctx['mitra'].ids), ('status', '=', 'dibayar'),
                 ('jatuh_tempo', '>=', ta), ('jatuh_tempo', '<=', tb)],
                'periode_id', ['pokok:sum', 'jasa:sum']).items():
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
            v['arus'] = v['simpanan'] + v['kembali'] + v['jasa'] + v['admin'] - v['cair']
            baris_periode.append(v)
        per_periode = bagian(_('Arus Dana per Periode'), [
            kolom('periode', 'Periode'), kolom('simpanan', 'Simpanan Diterima', 'rp', True),
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
                {'label': _('Total Simpanan Anggota'), 'nilai': total_simpanan, 'tipe': 'rp', 'sub': _('akhir periode')},
                {'label': _('Piutang Pinjaman'), 'nilai': piutang_akhir, 'tipe': 'rp', 'sub': _('sisa pokok, akhir periode')},
                {'label': _('Pendapatan Platform'), 'nilai': jasa + admin, 'tipe': 'rp', 'sub': _('jasa + biaya admin, periode ini')},
                {'label': _('Arus Dana Bersih'), 'nilai': arus, 'tipe': 'rp', 'sub': _('penerimaan − pencairan, periode ini')},
                {'label': _('Anggota Aktif'), 'nilai': anggota_aktif, 'tipe': 'angka', 'sub': _('saat ini')},
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
        """Ringkasan per mitra: simpanan, piutang, pendapatan, tunggakan."""
        simpanan = defaultdict(float)
        for b in baris_saldo:
            simpanan[b['mitra'].id] += b['simpanan']
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
            kolom('simpanan', 'Simpanan', 'rp', True), kolom('piutang', 'Piutang Pinjaman', 'rp', True),
            kolom('pendapatan', 'Pendapatan Platform', 'rp', True), kolom('tunggakan', 'Tunggakan', 'rp', True)],
            [{'mitra': m.name, 'anggota': aktif.get(m.id, [0])[0], 'simpanan': simpanan[m.id],
              'piutang': piutang[m.id], 'pendapatan': pendapatan[m.id], 'tunggakan': tunggakan[m.id]}
             for m in ctx['mitra']])

    # ------------------------------------------------------------------
    # 2. Laporan simpanan
    # ------------------------------------------------------------------
    def _laporan_simpanan(self, ctx):
        ta, tb = ctx['ta'], ctx['tb']
        baris = self._baris_tagihan(ctx, sampai=tb, status='dibayar')
        sebelum = [b for b in baris if b['periode'].tanggal_mulai < ta]
        masuk = [b for b in baris if b['periode'].tanggal_mulai >= ta]
        awal = sum(b['simpanan'] for b in sebelum)
        diterima = {k: sum(b[k] for b in masuk) for k in ('simpok', 'simwa', 'simsula')}
        total_masuk = sum(diterima.values())
        per_anggota = not ctx['per_mitra']

        Line = self.env['koperasi.tagihan.line']
        kunci = 'anggota_id' if per_anggota else 'mitra_id'
        dasar = ctx['dm'] + [('tagihan_id.state', '!=', 'draft'), ('status', '=', 'dibayar'),
                              ('tagihan_id.periode', '<=', tb)]
        agregat = ['simpok:sum', 'simwa:sum', 'simsula:sum']
        before = self._kelompok('koperasi.tagihan.line', dasar + [('tagihan_id.periode', '<', ta)], kunci, agregat)
        within = self._kelompok('koperasi.tagihan.line', dasar + [('tagihan_id.periode', '>=', ta)], kunci, agregat)
        if per_anggota:
            Anggota = self.env['koperasi.anggota']
            sasaran = Anggota.search(ctx['dm'] + [('state', '=', 'aktif')]) | Anggota.browse(set(before) | set(within))
            nama = lambda r: (r.name, r.nomor_anggota or '')
            label = _('Simpanan per Anggota')
            kolom_awal = [kolom('nama', 'Anggota'), kolom('kode', 'No. Anggota', 'kode')]
        else:
            sasaran = ctx['mitra']
            nama = lambda r: (r.name, r.kode or '')
            label = _('Simpanan per Mitra')
            kolom_awal = [kolom('nama', 'Mitra'), kolom('kode', 'Kode', 'kode')]
        rows = []
        for r in sasaran:
            b = before.get(r.id, [0, 0, 0])
            w = within.get(r.id, [0, 0, 0])
            rows.append({
                'nama': nama(r)[0], 'kode': nama(r)[1], 'awal': sum(b), 'simpok': w[0], 'simwa': w[1],
                'simsula': w[2], 'diterima': sum(w), 'akhir': sum(b) + sum(w)})
        rows.sort(key=lambda x: -x['akhir'])
        tabel = bagian(label, kolom_awal + [
            kolom('awal', 'Saldo Awal', 'rp', True), kolom('simpok', 'Pokok', 'rp', True),
            kolom('simwa', 'Wajib', 'rp', True), kolom('simsula', 'Sukarela', 'rp', True),
            kolom('diterima', 'Diterima', 'rp', True), kolom('akhir', 'Saldo Akhir', 'rp', True)], rows)

        per = {p.id: {'periode': p.name, 'simpok': 0, 'simwa': 0, 'simsula': 0} for p in ctx['periode_ids']}
        for b in masuk:
            if b['periode'].id in per:
                for k in ('simpok', 'simwa', 'simsula'):
                    per[b['periode'].id][k] += b[k]
        saldo, per_periode = awal, []
        for v in per.values():
            v['diterima'] = v['simpok'] + v['simwa'] + v['simsula']
            saldo += v['diterima']
            v['saldo'] = saldo
            per_periode.append(v)
        tabel_periode = bagian(_('Penerimaan Simpanan per Periode'), [
            kolom('periode', 'Periode'), kolom('simpok', 'Pokok', 'rp', True), kolom('simwa', 'Wajib', 'rp', True),
            kolom('simsula', 'Sukarela', 'rp', True), kolom('diterima', 'Diterima', 'rp', True),
            kolom('saldo', 'Saldo Kumulatif', 'rp')], per_periode, catatan=_(
            'Saldo kumulatif = saldo awal %s ditambah penerimaan tiap periode.', self._rp(awal)))
        return {
            'judul': _('Laporan Simpanan'),
            'subjudul': _('Saldo dan penerimaan simpanan pokok, wajib, dan sukarela'),
            'ringkasan': [
                {'label': _('Saldo Awal'), 'nilai': awal, 'tipe': 'rp', 'sub': _('sebelum %s', ctx['p_dari'].name)},
                {'label': _('Simpanan Diterima'), 'nilai': total_masuk, 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Saldo Akhir'), 'nilai': awal + total_masuk, 'tipe': 'rp', 'sub': _('akhir %s', ctx['p_sampai'].name)},
                {'label': _('Pokok / Wajib / Sukarela'), 'nilai': None, 'tipe': 'teks',
                 'sub': ' / '.join(self._rp(diterima[k]) for k in ('simpok', 'simwa', 'simsula'))},
            ],
            'bagian': [tabel, tabel_periode],
            'catatan': self._catatan_umum() + [
                _('Simpanan dihitung dari tagihan yang sudah divalidasi Dibayar, menurut periode tagihannya.')],
        }

    # ------------------------------------------------------------------
    # 3. Laporan pinjaman
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
            'status': label_status[r['rec'].state],
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
                _('Pencairan menurut tanggal pencairan; angsuran menurut periode jatuh tempo dan status Dibayar.')],
        }

    # ------------------------------------------------------------------
    # 4. Tagihan & kolektibilitas
    # ------------------------------------------------------------------
    def _laporan_tagihan(self, ctx):
        baris = self._baris_tagihan(ctx, sejak=ctx['ta'], sampai=ctx['tb'])
        jumlah = lambda sts, ambil=lambda b: b['asli']: sum(ambil(b) for b in baris if b['status'] in sts)
        ditagihkan = sum(b['asli'] for b in baris)
        diterima, belum, menunggu = jumlah(('dibayar',)), jumlah(('belum',)), jumlah(('menunggu',))
        kolek = lambda d, b: round(d * 100 / (d + b), 1) if (d + b) else None

        per = {p.id: {'periode': p.name, 'n': 0, 'ditagihkan': 0, 'diterima': 0, 'belum': 0, 'menunggu': 0}
               for p in ctx['periode_ids']}
        tagihan_per = defaultdict(set)
        for b in baris:
            v = per.get(b['periode'].id)
            if not v:
                continue
            tagihan_per[b['periode'].id].add(b['tagihan'].id)
            v['ditagihkan'] += b['asli']
            v[{'dibayar': 'diterima', 'belum': 'belum', 'menunggu': 'menunggu'}[b['status']]] += b['asli']
        rows = []
        for p_id, v in per.items():
            v['n'] = len(tagihan_per[p_id])
            v['kolek'] = kolek(v['diterima'], v['belum'])
            rows.append(v)
        tabel_periode = bagian(_('Tagihan per Periode'), [
            kolom('periode', 'Periode'), kolom('n', 'Jumlah Tagihan', 'angka', True),
            kolom('ditagihkan', 'Ditagihkan', 'rp', True), kolom('diterima', 'Diterima', 'rp', True),
            kolom('belum', 'Belum Dibayar', 'rp', True), kolom('menunggu', 'Belum Divalidasi', 'rp', True),
            kolom('kolek', 'Kolektibilitas', 'persen')], rows, catatan=_(
            'Kolektibilitas = diterima ÷ (diterima + belum dibayar), hanya dari baris yang sudah divalidasi.'))

        tunggak = self._tunggakan_per_anggota(ctx)
        bagian_laporan = [tabel_periode]
        if ctx['per_mitra']:
            per_mitra = {m.id: {'mitra': m.name, 'ditagihkan': 0, 'diterima': 0, 'belum': 0, 'menunggu': 0, 'tunggakan': 0}
                         for m in ctx['mitra']}
            for b in baris:
                v = per_mitra.get(b['mitra'].id)
                if v:
                    v['ditagihkan'] += b['asli']
                    v[{'dibayar': 'diterima', 'belum': 'belum', 'menunggu': 'menunggu'}[b['status']]] += b['asli']
            for a, t in tunggak.items():
                if a.mitra_id.id in per_mitra:
                    per_mitra[a.mitra_id.id]['tunggakan'] += t
            for v in per_mitra.values():
                v['kolek'] = kolek(v['diterima'], v['belum'])
            bagian_laporan.append(bagian(_('Tagihan per Mitra'), [
                kolom('mitra', 'Mitra'), kolom('ditagihkan', 'Ditagihkan', 'rp', True), kolom('diterima', 'Diterima', 'rp', True),
                kolom('belum', 'Belum Dibayar', 'rp', True), kolom('menunggu', 'Belum Divalidasi', 'rp', True),
                kolom('kolek', 'Kolektibilitas', 'persen'), kolom('tunggakan', 'Tunggakan Saat Ini', 'rp', True)],
                per_mitra.values()))
        bagian_laporan.append(bagian(_('Tunggakan Saat Ini per Anggota'), [
            kolom('nomor', 'No. Anggota', 'kode'), kolom('nama', 'Anggota'), kolom('mitra', 'Mitra'),
            kolom('tunggakan', 'Tunggakan', 'rp', True)],
            [{'nomor': a.nomor_anggota or '', 'nama': a.name, 'mitra': a.mitra_id.name, 'tunggakan': t}
             for a, t in sorted(tunggak.items(), key=lambda x: -x[1]) if t],
            kosong=_('Tidak ada anggota yang menunggak.'),
            catatan=_('Keadaan saat ini, tidak terpengaruh oleh pilihan periode.')))
        return {
            'judul': _('Tagihan & Kolektibilitas'),
            'subjudul': _('Penagihan dan pembayaran simpanan serta angsuran pinjaman'),
            'ringkasan': [
                {'label': _('Ditagihkan'), 'nilai': ditagihkan, 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Diterima'), 'nilai': diterima, 'tipe': 'rp', 'sub': _('sudah dibayar')},
                {'label': _('Belum Dibayar'), 'nilai': belum, 'tipe': 'rp', 'sub': _('+ %s belum divalidasi', self._rp(menunggu))},
                {'label': _('Kolektibilitas'), 'nilai': kolek(diterima, belum), 'tipe': 'persen', 'sub': _('dari yang sudah divalidasi')},
                {'label': _('Tunggakan Saat Ini'), 'nilai': sum(tunggak.values()), 'tipe': 'rp', 'sub': _('semua periode')},
            ],
            'bagian': bagian_laporan,
            'catatan': self._catatan_umum() + [
                _('Hanya tagihan yang sudah ditagihkan (bukan Draft). Jumlah = simpanan + angsuran pokok + angsuran jasa; tunggakan terbawa tidak dihitung dua kali.')],
        }

    # ------------------------------------------------------------------
    # 5. Buku simpanan anggota
    # ------------------------------------------------------------------
    def _laporan_anggota(self, ctx):
        a = ctx['anggota']
        if not a:
            return {'judul': _('Buku Simpanan Anggota'), 'subjudul': _('Mutasi simpanan dan pinjaman satu anggota'),
                    'ringkasan': [], 'bagian': [], 'catatan': [], 'perlu_anggota': True}
        ta, tb = ctx['ta'], ctx['tb']
        Line = self.env['koperasi.tagihan.line']
        lines = Line.search([('anggota_id', '=', a.id), ('tagihan_id.state', '!=', 'draft'),
                             ('tagihan_id.periode', '<=', tb)]).sorted(lambda l: (l.periode, l.id))
        label = dict(STATUS_BARIS)
        saldo = sum(l.simpok + l.simwa + l.simsula for l in lines
                    if l.status == 'dibayar' and l.periode < ta)
        awal = saldo
        rows = [{'periode': _('Saldo awal'), 'simpok': None, 'simwa': None, 'simsula': None, 'diterima': None,
                 'saldo': saldo, 'status': '', '_gaya': 'judul'}]
        masuk = [0, 0, 0]
        for l in lines:
            if l.periode < ta:
                continue
            dibayar = l.status == 'dibayar'
            diterima = (l.simpok + l.simwa + l.simsula) if dibayar else 0
            saldo += diterima
            for i, v in enumerate((l.simpok, l.simwa, l.simsula)):
                masuk[i] += v if dibayar else 0
            rows.append({'periode': l.periode_id.name, 'simpok': l.simpok, 'simwa': l.simwa, 'simsula': l.simsula,
                         'diterima': diterima, 'saldo': saldo, 'status': label.get(l.status, l.status)})
        mutasi = bagian(_('Mutasi Simpanan'), [
            kolom('periode', 'Periode'), kolom('simpok', 'Pokok', 'rp'), kolom('simwa', 'Wajib', 'rp'),
            kolom('simsula', 'Sukarela', 'rp'), kolom('diterima', 'Diterima (Dibayar)', 'rp', True),
            kolom('saldo', 'Saldo', 'rp'), kolom('status', 'Status Bayar', 'status')], rows, catatan=_(
            'Jumlah Pokok/Wajib/Sukarela adalah yang ditagihkan; saldo hanya bertambah bila status Dibayar.'),
            kosong=_('Belum ada tagihan.'))

        pinjaman = a.pinjaman_ids.filtered(lambda p: p.state in ('berjalan', 'lunas')).sorted('tanggal_cair')
        daftar = [{'nomor': p.name, 'produk': p.produk_id.name, 'pokok': p.pokok, 'tenor': p.tenor,
                   'cair': fields.Date.to_string(p.tanggal_cair), 'angsuran': p.angsuran_bulan,
                   'dibayar': '%d / %d' % (p.angsuran_dibayar, p.tenor), 'sisa': p.sisa_pokok,
                   'status': dict(STATE_PINJAMAN)[p.state]} for p in pinjaman]
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
            'judul': _('Buku Simpanan Anggota'),
            'subjudul': '%s%s' % (a.name, ' — %s' % a.nomor_anggota if a.nomor_anggota else ''),
            'ringkasan': [
                {'label': _('Saldo Awal'), 'nilai': awal, 'tipe': 'rp', 'sub': _('sebelum %s', ctx['p_dari'].name)},
                {'label': _('Simpanan Diterima'), 'nilai': sum(masuk), 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Saldo Akhir'), 'nilai': saldo, 'tipe': 'rp', 'sub': _('akhir %s', ctx['p_sampai'].name)},
                {'label': _('Sisa Pinjaman'), 'nilai': sum(pinjaman.mapped('sisa_pokok')), 'tipe': 'rp', 'sub': _('saat ini')},
                {'label': _('Tunggakan'), 'nilai': sum(tunggakan.values()), 'tipe': 'rp', 'sub': _('saat ini')},
            ],
            'identitas': [
                (_('Nama'), a.name), (_('No. Anggota'), a.nomor_anggota or '-'), (_('NIP / ID Karyawan'), a.nip or '-'),
                (_('Mitra'), a.mitra_id.name), (_('Jabatan'), a.jabatan or '-'),
                (_('Bergabung'), a.tanggal_bergabung.strftime('%d/%m/%Y') if a.tanggal_bergabung else '-')],
            'bagian': [mutasi, tabel_pinjaman, tabel_angsuran],
            'catatan': self._catatan_umum(),
        }

    # ------------------------------------------------------------------
    def _rp(self, nilai):
        return 'Rp %s' % '{:,.0f}'.format(nilai or 0).replace(',', '.')

    def _catatan_umum(self):
        return [_('Laporan manajemen yang dihitung dari data operasional koperasi, bukan Neraca atau Laba Rugi '
                  'akuntansi (belum ada pembukuan kas/bank dan biaya operasional).')]
