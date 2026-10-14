"""Laporan keuangan dari jurnal pembukuan: Neraca, Laba Rugi, Arus Kas, Neraca Saldo.

Memakai struktur laporan yang sama dengan koperasi_laporan.py, sehingga tampil di layar,
PDF, dan Excel tanpa penampil khusus. Hanya untuk operator pusat (lihat JENIS_PEMBUKUAN).
Semua angka berasal dari baris jurnal berstatus Terposting.
"""
from collections import defaultdict
from datetime import date

from odoo import fields, models, _

from .koperasi_laporan import bagian, buka, kolom
from .koperasi_pembukuan import ARUS_KAS, JENIS_AKUN, SALDO_DEBIT, SUMBER_JURNAL
from .koperasi_shu import POS_LABA

LABA_RUGI = ('pendapatan', 'beban')
BATAS_BARIS_JURNAL = 300
K_DUA = [kolom('uraian', 'Uraian'), kolom('jumlah', 'Jumlah', 'rp')]


def isi(uraian, jumlah=None, gaya=None, buka_=None):
    return {'uraian': uraian, 'jumlah': jumlah, '_gaya': gaya, '_buka': buka_}


class KoperasiLaporan(models.AbstractModel):
    _inherit = 'koperasi.laporan'

    # ------------------------------------------------------------------
    # Bahan hitung
    # ------------------------------------------------------------------
    def _dom_buku(self, ctx, sejak=None, sampai=None):
        """Baris jurnal terposting; bila satu mitra dipilih, hanya jurnal bertanda mitra itu."""
        domain = [('state', '=', 'posted')]
        if ctx['mitra_dipilih']:
            domain.append(('mitra_id', 'in', ctx['mitra'].ids))
        if sejak:
            domain.append(('tanggal', '>=', fields.Date.to_string(sejak)))
        if sampai:
            domain.append(('tanggal', '<=', fields.Date.to_string(sampai)))
        return domain

    def _mutasi_akun(self, domain):
        """{akun: (debit, kredit)}"""
        return {akun: (d or 0, k or 0) for akun, d, k in self.env['koperasi.jurnal.line']._read_group(
            domain, ['akun_id'], ['debit:sum', 'kredit:sum'])}

    @staticmethod
    def _saldo_normal(akun, debit, kredit):
        """Saldo menurut sisi normal akun: aset dan beban di debit, lainnya di kredit."""
        return debit - kredit if akun.jenis in SALDO_DEBIT else kredit - debit

    def _buka_akun(self, ctx, akun, sejak=None, sampai=None):
        return {'model': 'koperasi.jurnal.line', 'nama': akun.display_name,
                'domain': self._dom_buku(ctx, sejak, sampai) + [('akun_id', '=', akun.id)]}

    def _catatan_buku(self, ctx):
        catatan = [
            _('Disusun dari jurnal yang sudah diposting. Basis kas: angsuran dan jasa pinjaman '
              'diakui saat pembayarannya divalidasi; pinjaman diakui saat dicairkan.'),
            _('Pencairan pinjaman dan penerimaan tagihan dijurnal otomatis. Modal, biaya operasional, dan '
              'transaksi lain hanya muncul bila dicatat lewat menu Pembukuan > Jurnal.'),
        ]
        if ctx['mitra_dipilih']:
            catatan.append(_('Disaring untuk %s: hanya jurnal yang bertanda mitra ini. Jurnal umum tanpa mitra '
                             'tidak ikut, jadi ini bukan laporan keuangan koperasi secara utuh.', ctx['mitra_label']))
        return catatan

    # ------------------------------------------------------------------
    # Neraca
    # ------------------------------------------------------------------
    def _laporan_neraca(self, ctx):
        tb = ctx['tb']
        p = ctx['p_sampai']
        awal_tahun = date(p.tahun, 1, 1)
        mutasi = self._mutasi_akun(self._dom_buku(ctx, sampai=tb))
        saldo = {akun: self._saldo_normal(akun, d, k) for akun, (d, k) in mutasi.items()}
        urut = sorted(saldo, key=lambda a: a.kode)
        mata_uang = self.env.company.currency_id

        def kelompok(jenis):
            return [a for a in urut if a.jenis == jenis and not mata_uang.is_zero(saldo[a])]

        def baris_akun(akun):
            return isi(akun.display_name, saldo[akun], None, self._buka_akun(ctx, akun, sampai=tb))

        # laba tahun yang belum tutup buku belum ada di akun ekuitas, jadi dihitung di sini
        shu = sum(saldo[a] if a.jenis == 'pendapatan' else -saldo[a] for a in urut if a.jenis in LABA_RUGI)
        lalu = self._mutasi_akun(self._dom_buku(ctx, sampai=awal_tahun.replace(year=p.tahun - 1, month=12, day=31))
                                 + [('jenis_akun', 'in', LABA_RUGI)])
        shu_lalu = sum(k - d for d, k in lalu.values())
        shu_jalan = shu - shu_lalu

        aset, kewajiban, ekuitas = kelompok('aset'), kelompok('kewajiban'), kelompok('ekuitas')
        t_aset = sum(saldo[a] for a in aset)
        t_kewajiban = sum(saldo[a] for a in kewajiban)
        t_ekuitas = sum(saldo[a] for a in ekuitas) + shu
        kas = sum(saldo[a] for a in aset if a.kas)
        piutang = sum(saldo[a] for a in aset if not a.kas and a.arus_kas == 'operasi')
        selisih = t_aset - t_kewajiban - t_ekuitas
        seimbang = mata_uang.is_zero(selisih)

        rows = [isi(_('ASET'), None, 'judul')] + [baris_akun(a) for a in aset] + [
            isi(_('Total Aset'), t_aset, 'total'),
            isi(_('KEWAJIBAN'), None, 'judul')] + [baris_akun(a) for a in kewajiban] + [
            isi(_('Total Kewajiban'), t_kewajiban, 'total'),
            isi(_('EKUITAS'), None, 'judul')] + [baris_akun(a) for a in ekuitas]
        if not mata_uang.is_zero(shu_lalu):
            rows.append(isi(_('Laba tahun sebelumnya (belum tutup buku)'), shu_lalu))
        rows += [
            isi(_('Laba tahun berjalan (%s)', p.tahun), shu_jalan),
            isi(_('Total Ekuitas'), t_ekuitas, 'total'),
            isi(_('Total Kewajiban dan Ekuitas'), t_kewajiban + t_ekuitas, 'total'),
        ]
        catatan = self._catatan_buku(ctx) + [
            _('Laba tahun yang belum tutup buku dihitung dari akun pendapatan dan beban sampai akhir periode '
              'laporan, sebelum pajak dan pembagian. Setelah tutup buku ditetapkan (menu Pembukuan > Tutup '
              'Buku & Laba), laba dipindahkan ke Laba Ditahan dan pos pembagiannya.')]
        if kas < 0:
            catatan.insert(0, _(
                'Saldo kas dan bank negatif (%s): dana yang disalurkan lebih besar daripada dana yang tercatat '
                'masuk. Catat sumber dananya (setoran modal atau pinjaman pihak ketiga) lewat menu '
                'Pembukuan > Jurnal.', self._rp_minus(kas)))
        terbuka = self.env['koperasi.periode'].search(
            [('tanggal_mulai', '<=', p.tanggal_mulai), ('state', '!=', 'ditutup')], order='tanggal_mulai')
        return {
            'judul': _('Neraca'),
            'subjudul': _('Posisi keuangan per %s', tb.strftime('%d/%m/%Y')),
            'status_periode': self._status_periode(terbuka),
            'ringkasan': [
                {'label': _('Total Aset'), 'nilai': t_aset, 'tipe': 'rp', 'sub': _('per akhir %s', p.name)},
                {'label': _('Kas dan Bank'), 'nilai': kas, 'tipe': 'rp', 'sub': _('saldo akun kas/bank')},
                {'label': _('Piutang Pinjaman'), 'nilai': piutang, 'tipe': 'rp', 'sub': _('sisa pokok pinjaman')},
                {'label': _('Total Kewajiban'), 'nilai': t_kewajiban, 'tipe': 'rp', 'sub': _('utang dan pembagian laba terutang')},
                {'label': _('Total Ekuitas'), 'nilai': t_ekuitas, 'tipe': 'rp', 'sub': _('termasuk laba %s', self._rp_minus(shu))},
                {'label': _('Keseimbangan'), 'nilai': None, 'tipe': 'teks',
                 'sub': _('Seimbang') if seimbang else _('Selisih %s', self._rp_minus(selisih))},
            ],
            'bagian': [bagian(_('Neraca per Akhir %s', p.name), K_DUA, rows, total=False,
                              kosong=_('Belum ada jurnal sampai periode ini.'))],
            'catatan': catatan,
        }

    # ------------------------------------------------------------------
    # Laba rugi
    # ------------------------------------------------------------------
    def _laporan_labarugi(self, ctx):
        ta, tb = ctx['ta'], ctx['tb']
        Line = self.env['koperasi.jurnal.line']
        # jurnal penutup menolkan pendapatan dan beban; laba rugi harus tetap menampilkannya
        dasar = self._dom_buku(ctx, ta, tb) + [('jenis_akun', 'in', LABA_RUGI), ('sumber', '!=', 'penutup')]
        mutasi = self._mutasi_akun(dasar)
        saldo = {akun: self._saldo_normal(akun, d, k) for akun, (d, k) in mutasi.items()}
        urut = sorted(saldo, key=lambda a: a.kode)
        pendapatan = [a for a in urut if a.jenis == 'pendapatan']
        beban = [a for a in urut if a.jenis == 'beban']
        t_pendapatan = sum(saldo[a] for a in pendapatan)
        t_beban = sum(saldo[a] for a in beban)
        shu = t_pendapatan - t_beban

        def baris_akun(akun):
            return isi(akun.display_name, saldo[akun], None, self._buka_akun(ctx, akun, ta, tb))

        rows = [isi(_('PENDAPATAN'), None, 'judul')] + [baris_akun(a) for a in pendapatan] + [
            isi(_('Total Pendapatan'), t_pendapatan, 'total'),
            isi(_('BEBAN'), None, 'judul')] + [baris_akun(a) for a in beban] + [
            isi(_('Total Beban'), t_beban, 'total'),
            isi(_('Laba Bersih'), shu, 'total'),
        ]
        tabel = bagian(_('Laba Rugi (%s)', ctx['periode_label']), K_DUA, rows, total=False)

        def per(kunci, awal):
            hasil = dict(awal)
            for rec, jenis, d, k in Line._read_group(dasar, [kunci, 'jenis_akun'], ['debit:sum', 'kredit:sum']):
                v = hasil.setdefault(rec.id, {'nama': _('Umum (tanpa mitra)'), 'pendapatan': 0, 'beban': 0})
                if jenis == 'pendapatan':
                    v['pendapatan'] += (k or 0) - (d or 0)
                else:
                    v['beban'] += (d or 0) - (k or 0)
            for v in hasil.values():
                v['shu'] = v['pendapatan'] - v['beban']
            return list(hasil.values())

        kolom_rinci = lambda label: [
            kolom('nama', label), kolom('pendapatan', 'Pendapatan', 'rp', True),
            kolom('beban', 'Beban', 'rp', True), kolom('shu', 'Laba', 'rp', True)]
        bagian_laporan = [tabel, bagian(_('Laba per Periode'), kolom_rinci('Periode'), per(
            'periode_id', {p.id: {'nama': p.name, 'pendapatan': 0, 'beban': 0} for p in ctx['periode_ids']}))]
        if ctx['per_mitra']:
            bagian_laporan.append(bagian(_('Laba per Mitra'), kolom_rinci('Mitra'), per(
                'mitra_id', {m.id: {'nama': m.name, 'pendapatan': 0, 'beban': 0, '_buka': buka(m)} for m in ctx['mitra']}),
                catatan=_('Menurut tanda mitra pada jurnal. Beban umumnya tidak bertanda mitra dan masuk baris Umum.')))
        return {
            'judul': _('Laba Rugi'),
            'subjudul': _('Pendapatan, beban, dan laba bersih'),
            'ringkasan': [
                {'label': _('Total Pendapatan'), 'nilai': t_pendapatan, 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Total Beban'), 'nilai': t_beban, 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Laba Bersih'), 'nilai': shu, 'tipe': 'rp', 'sub': _('pendapatan − beban')},
                {'label': _('Rasio Beban'), 'nilai': round(t_beban * 100 / t_pendapatan, 1) if t_pendapatan else None,
                 'tipe': 'persen', 'sub': _('beban terhadap pendapatan') if t_pendapatan else _('belum ada pendapatan')},
            ],
            'bagian': bagian_laporan,
            'catatan': self._catatan_buku(ctx) + [
                _('Laba sebelum pajak dan sebelum pembagian.')] + (
                [] if beban else [_('Belum ada beban yang dijurnal pada periode ini, sehingga laba sama dengan pendapatan.')]),
        }

    # ------------------------------------------------------------------
    # Arus kas (metode langsung)
    # ------------------------------------------------------------------
    def _laporan_aruskas(self, ctx):
        ta, tb = ctx['ta'], ctx['tb']
        Line = self.env['koperasi.jurnal.line']
        dom_rentang = self._dom_buku(ctx, ta, tb)
        # Jurnal yang menyentuh kas: uang masuk/keluar digolongkan menurut akun lawannya.
        # Pemindahan antar akun kas saling menghapus dan tidak muncul.
        per_akun = defaultdict(lambda: [0, 0])
        for akun, d, k in Line._read_group(
                dom_rentang + [('akun_id.kas', '=', False), ('jurnal_id.line_ids.akun_id.kas', '=', True)],
                ['akun_id'], ['debit:sum', 'kredit:sum']):
            per_akun[akun] = [k or 0, d or 0]  # kredit akun lawan = kas masuk; debit = kas keluar
        K = [kolom('uraian', 'Uraian'), kolom('masuk', 'Kas Masuk', 'rp'), kolom('keluar', 'Kas Keluar', 'rp'),
             kolom('bersih', 'Bersih', 'rp')]

        def baris(uraian, masuk=None, keluar=None, gaya=None, buka_=None):
            bersih = None if masuk is None else masuk - keluar
            return {'uraian': uraian, 'masuk': masuk, 'keluar': keluar, 'bersih': bersih, '_gaya': gaya, '_buka': buka_}

        rows, total_masuk, total_keluar = [], 0, 0
        for kode, label in ARUS_KAS:
            akun_kelompok = sorted((a for a in per_akun if a.arus_kas == kode), key=lambda a: a.kode)
            if not akun_kelompok:
                continue
            rows.append(baris(label.upper(), gaya='judul'))
            masuk = keluar = 0
            for a in akun_kelompok:
                m, kl = per_akun[a]
                rows.append(baris(a.display_name, m, kl, None, self._buka_akun(ctx, a, ta, tb)))
                masuk += m
                keluar += kl
            rows.append(baris(_('Kas bersih dari %s', label.lower()), masuk, keluar, 'total'))
            total_masuk += masuk
            total_keluar += keluar
        bersih = total_masuk - total_keluar
        if rows:
            rows.append(baris(_('Kenaikan (penurunan) bersih kas'), total_masuk, total_keluar, 'total'))

        dom_kas = [('akun_id.kas', '=', True)]
        sebelum = ctx['sebelum']
        kas_awal = sum(d - k for d, k in self._mutasi_akun(self._dom_buku(ctx, sampai=sebelum) + dom_kas).values())
        kas_akhir = sum(d - k for d, k in self._mutasi_akun(self._dom_buku(ctx, sampai=tb) + dom_kas).values())
        selisih = kas_awal + bersih - kas_akhir
        rekon = bagian(_('Saldo Kas dan Bank'), K_DUA, [
            isi(_('Saldo awal (sebelum %s)', ctx['p_dari'].name), kas_awal),
            isi(_('Kenaikan (penurunan) bersih kas'), bersih),
            isi(_('Saldo akhir (%s)', ctx['p_sampai'].name), kas_awal + bersih, 'total'),
        ], total=False, catatan=None if self.env.company.currency_id.is_zero(selisih) else _(
            'Selisih terhadap saldo buku besar kas/bank: %s', self._rp_minus(selisih)))

        per = {p.id: {'periode': p.name, 'masuk': 0, 'keluar': 0} for p in ctx['periode_ids']}
        for periode, d, k in Line._read_group(dom_rentang + dom_kas, ['periode_id'], ['debit:sum', 'kredit:sum']):
            if periode.id in per:
                per[periode.id]['masuk'] += d or 0
                per[periode.id]['keluar'] += k or 0
        saldo, rows_periode = kas_awal, []
        for v in per.values():
            v['bersih'] = v['masuk'] - v['keluar']
            saldo += v['bersih']
            v['saldo'] = saldo
            rows_periode.append(v)
        tabel_periode = bagian(_('Mutasi Kas dan Bank per Periode'), [
            kolom('periode', 'Periode'), kolom('masuk', 'Kas Masuk', 'rp', True), kolom('keluar', 'Kas Keluar', 'rp', True),
            kolom('bersih', 'Bersih', 'rp', True), kolom('saldo', 'Saldo Akhir', 'rp')], rows_periode,
            catatan=_('Mutasi akun kas/bank menurut buku besar; pemindahan antar akun kas ikut terhitung di kedua sisi.'))
        return {
            'judul': _('Arus Kas'),
            'subjudul': _('Uang masuk dan keluar menurut aktivitas, metode langsung'),
            'ringkasan': [
                {'label': _('Saldo Kas Awal'), 'nilai': kas_awal, 'tipe': 'rp', 'sub': _('sebelum %s', ctx['p_dari'].name)},
                {'label': _('Kas Masuk'), 'nilai': total_masuk, 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Kas Keluar'), 'nilai': total_keluar, 'tipe': 'rp', 'sub': ctx['periode_label']},
                {'label': _('Kenaikan Bersih'), 'nilai': bersih, 'tipe': 'rp', 'sub': _('masuk − keluar')},
                {'label': _('Saldo Kas Akhir'), 'nilai': kas_awal + bersih, 'tipe': 'rp', 'sub': _('akhir %s', ctx['p_sampai'].name)},
            ],
            'bagian': [bagian(_('Arus Kas (%s)', ctx['periode_label']), K, rows, total=False,
                              kosong=_('Tidak ada kas masuk atau keluar pada periode ini.')), rekon, tabel_periode],
            'catatan': self._catatan_buku(ctx) + [
                _('Penyaluran dan pengembalian pinjaman digolongkan aktivitas operasi karena merupakan usaha utama '
                  'koperasi; modal dan utang pihak ketiga digolongkan aktivitas pendanaan.'),
                _('Biaya administrasi dipotong dari dana pencairan: pencairan dicatat keluar sebesar pokok dan '
                  'biaya administrasi dicatat masuk.')],
        }

    # ------------------------------------------------------------------
    # Neraca saldo dan daftar jurnal
    # ------------------------------------------------------------------
    def _laporan_bukubesar(self, ctx):
        ta, tb = ctx['ta'], ctx['tb']
        Jurnal = self.env['koperasi.jurnal']
        awal = self._mutasi_akun(self._dom_buku(ctx, sampai=ctx['sebelum']))
        mutasi = self._mutasi_akun(self._dom_buku(ctx, ta, tb))
        label_jenis = dict(JENIS_AKUN)
        rows = []
        for akun in sorted(set(awal) | set(mutasi), key=lambda a: a.kode):
            d0, k0 = awal.get(akun, (0, 0))
            d, k = mutasi.get(akun, (0, 0))
            rows.append({
                'kode': akun.kode, 'akun': akun.name, 'jenis': label_jenis[akun.jenis],
                'awal': self._saldo_normal(akun, d0, k0), 'debit': d, 'kredit': k,
                'akhir': self._saldo_normal(akun, d0 + d, k0 + k), '_buka': self._buka_akun(ctx, akun, ta, tb)})
        tabel = bagian(_('Neraca Saldo'), [
            kolom('kode', 'Kode', 'kode'), kolom('akun', 'Akun'), kolom('jenis', 'Jenis'),
            kolom('awal', 'Saldo Awal', 'rp'), kolom('debit', 'Debit', 'rp', True),
            kolom('kredit', 'Kredit', 'rp', True), kolom('akhir', 'Saldo Akhir', 'rp')], rows,
            kosong=_('Belum ada jurnal sampai periode ini.'),
            catatan=_('Saldo ditampilkan menurut sisi normal akun: aset dan beban di debit; kewajiban, ekuitas, '
                      'dan pendapatan di kredit. Klik baris untuk melihat buku besarnya.'))

        dom_jurnal = [('state', '=', 'posted'), ('tanggal', '>=', ta), ('tanggal', '<=', tb)]
        dom_draft = [('state', '=', 'draft'), ('tanggal', '>=', ta), ('tanggal', '<=', tb)]
        if ctx['mitra_dipilih']:
            dom_jurnal.append(('mitra_id', 'in', ctx['mitra'].ids))
            dom_draft.append(('mitra_id', 'in', ctx['mitra'].ids))
        jumlah = Jurnal.search_count(dom_jurnal)
        label_sumber = dict(SUMBER_JURNAL)
        daftar = [{
            'nomor': j.name, 'tanggal': fields.Date.to_string(j.tanggal), 'keterangan': j.keterangan,
            'sumber': label_sumber[j.sumber], 'mitra': j.mitra_id.name or '', 'nilai': j.total_debit,
            '_buka': buka(j),
        } for j in Jurnal.search(dom_jurnal, order='tanggal, id', limit=BATAS_BARIS_JURNAL)]
        tabel_jurnal = bagian(_('Daftar Jurnal'), [
            kolom('nomor', 'Nomor', 'kode'), kolom('tanggal', 'Tanggal', 'tanggal'), kolom('keterangan', 'Keterangan'),
            kolom('sumber', 'Sumber'), kolom('mitra', 'Mitra'), kolom('nilai', 'Nilai', 'rp', True)], daftar,
            kosong=_('Tidak ada jurnal pada periode ini.'),
            catatan=_('Ditampilkan %(n)d jurnal pertama dari %(total)d.', n=BATAS_BARIS_JURNAL, total=jumlah)
            if jumlah > BATAS_BARIS_JURNAL else None)
        t_debit = sum(r['debit'] for r in rows)
        t_kredit = sum(r['kredit'] for r in rows)
        draft = Jurnal.search_count(dom_draft)
        return {
            'judul': _('Neraca Saldo & Jurnal'),
            'subjudul': _('Mutasi dan saldo tiap akun, serta jurnal pada periode laporan'),
            'ringkasan': [
                {'label': _('Jurnal Terposting'), 'nilai': jumlah, 'tipe': 'angka', 'sub': ctx['periode_label']},
                {'label': _('Total Debit'), 'nilai': t_debit, 'tipe': 'rp', 'sub': _('mutasi periode')},
                {'label': _('Total Kredit'), 'nilai': t_kredit, 'tipe': 'rp', 'sub': _('mutasi periode')},
                {'label': _('Keseimbangan'), 'nilai': None, 'tipe': 'teks',
                 'sub': _('Seimbang') if self.env.company.currency_id.is_zero(t_debit - t_kredit)
                 else _('Selisih %s', self._rp_minus(t_debit - t_kredit))},
                {'label': _('Jurnal Draft'), 'nilai': draft, 'tipe': 'angka',
                 'sub': _('belum diposting, tidak ikut dihitung') if draft else _('tidak ada')},
            ],
            'bagian': [tabel, tabel_jurnal],
            'catatan': self._catatan_buku(ctx),
        }

    # ------------------------------------------------------------------
    # Pembagian laba (per dokumen, dibuka dari form Tutup Buku & Laba)
    # ------------------------------------------------------------------
    def _laporan_shu(self, ctx):
        r = ctx['shu']
        if not r:
            return {'judul': _('Pembagian Laba'), 'subjudul': '', 'ringkasan': [], 'bagian': [], 'catatan': [],
                    'perlu_anggota': True}
        label_state = dict(r._fields['state'].selection)
        hitung = bagian(_('Hasil Usaha Tahun %s', r.tahun), K_DUA, [
            isi(_('Total pendapatan'), r.total_pendapatan),
            isi(_('Total beban'), r.total_beban),
            isi(_('Laba bersih'), r.shu, 'total'),
        ], total=False)
        pos = bagian(_('Pembagian Laba'), [
            kolom('pos', 'Pos'), kolom('persen', 'Persentase', 'persen'), kolom('nominal', 'Jumlah', 'rp', True),
            kolom('ket', 'Keterangan')],
            [{'pos': label, 'persen': r['persen_' + k], 'nominal': r['nominal_' + k], 'ket': {
                'pemodal': _('dibayarkan kepada pemodal'),
                'ditahan': _('tetap di usaha, menambah dana pinjaman'),
                'anggota': _('%d anggota, sebanding jasa + admin yang dihasilkannya', r.jumlah_anggota),
                'mitra': _('%d mitra, sebanding pendapatan dari anggotanya', r.jumlah_mitra),
            }[k]} for k, label, _a in POS_LABA],
            catatan=_('Jumlah mengikuti persentase, kecuali: imbalan anggota dan komisi mitra dibulatkan ke bawah '
                      'per rupiah per penerima, dan sisa pembulatan (atau rugi) masuk Laba Ditahan.'))
        tabel_mitra = bagian(_('Komisi per Mitra'), [
            kolom('mitra', 'Mitra'), kolom('dasar', 'Pendapatan dari Mitra', 'rp', True),
            kolom('porsi', 'Porsi', 'persen'), kolom('komisi', 'Komisi', 'rp', True)],
            [{'mitra': m.mitra_id.name, 'dasar': m.dasar_pendapatan,
              'porsi': round(m.dasar_pendapatan * 100 / sum(r.mitra_ids.mapped('dasar_pendapatan')), 1),
              'komisi': m.komisi, '_buka': buka(m.mitra_id)} for m in r.mitra_ids],
            kosong=_('Tidak ada komisi mitra.'),
            catatan=_('Pendapatan dari mitra = jasa pinjaman dan biaya administrasi dari anggota mitra itu.'))
        per_mitra = {}
        for l in r.line_ids:
            v = per_mitra.setdefault(l.mitra_id, {'mitra': l.mitra_id.name, 'n': 0, 'dasar': 0, 'total': 0})
            v['n'] += 1
            v['dasar'] += l.dasar_jasa
            v['total'] += l.total
        tabel_imbalan_mitra = bagian(_('Imbalan Anggota per Mitra'), [
            kolom('mitra', 'Mitra'), kolom('n', 'Anggota', 'angka', True),
            kolom('dasar', 'Jasa + Admin Dibayar', 'rp', True), kolom('total', 'Imbalan', 'rp', True)],
            sorted(per_mitra.values(), key=lambda v: -v['total']), kosong=_('Tidak ada imbalan anggota.'))
        tabel_anggota = bagian(_('Imbalan per Anggota'), [
            kolom('nomor', 'No. Anggota', 'kode'), kolom('nama', 'Anggota'), kolom('mitra', 'Mitra'),
            kolom('dasar', 'Jasa + Admin Dibayar', 'rp', True), kolom('total', 'Imbalan', 'rp', True)],
            [{'nomor': l.anggota_id.nomor_anggota or '', 'nama': l.anggota_id.name, 'mitra': l.mitra_id.name,
              'dasar': l.dasar_jasa, 'total': l.total, '_buka': buka(l.anggota_id)}
             for l in r.line_ids.sorted(lambda l: (l.mitra_id.name or '', l.anggota_id.name or ''))],
            kosong=_('Tidak ada imbalan anggota (laba nol, rugi, atau persentasenya nol).'))
        final = r.state != 'draft'
        return {
            'judul': _('Pembagian Laba'),
            'subjudul': _('Tahun buku %s', r.tahun),
            'nama_berkas': 'Pembagian Laba %s' % r.tahun,
            'periode_label': _('Tahun %s', r.tahun),
            'mitra_label': _('Semua Mitra'),
            'status_periode': {
                'final': final, 'label': label_state[r.state] if final else _('Simulasi'),
                'keterangan': _('ditetapkan %s', r.tanggal_tetap.strftime('%d/%m/%Y')) if final else _(
                    'belum ditetapkan; angka masih bisa berubah')},
            'ringkasan': [
                {'label': _('Laba Bersih'), 'nilai': r.shu, 'tipe': 'rp', 'sub': _('pendapatan − beban')},
                {'label': _('Modal Penyertaan'), 'nilai': r.modal_penyertaan, 'tipe': 'rp', 'sub': _('akhir tahun buku')},
                {'label': _('Laba terhadap Modal'), 'nilai': round(r.laba_atas_modal, 1) if r.modal_penyertaan > 0 else None,
                 'tipe': 'persen', 'sub': _('laba bersih ÷ modal') if r.modal_penyertaan > 0 else _('modal belum dicatat')},
                {'label': _('Imbal Hasil Pemodal'), 'nilai': r.nominal_pemodal, 'tipe': 'rp',
                 'sub': _('%s%% dari modal', ('%.1f' % r.imbal_atas_modal).replace('.', ',')) if r.modal_penyertaan > 0 else ''},
                {'label': _('Laba Ditahan'), 'nilai': r.nominal_ditahan, 'tipe': 'rp', 'sub': _('tetap di usaha')},
                {'label': _('Status'), 'nilai': None, 'tipe': 'teks', 'sub': label_state[r.state] if final else _('Simulasi')},
            ],
            'bagian': [hitung, pos, tabel_mitra, tabel_imbalan_mitra, tabel_anggota],
            'catatan': [
                _('Laba dihitung dari jurnal yang sudah diposting, sebelum pajak.'),
                _('Imbalan anggota dibagi sebanding jasa pinjaman dan biaya administrasi yang dihasilkannya '
                  'sendiri selama tahun buku. Komisi mitra dibagi sebanding pendapatan yang dihasilkan '
                  'anggotanya selama tahun buku.')] + (
                [] if final else [_('SIMULASI: belum ditetapkan. Untuk tahun yang belum selesai, laba dan rasio terhadap '
                                    'modal adalah angka sampai hari ini, belum disetahunkan.')]),
        }

    def _rp_minus(self, nilai):
        return ('-' if (nilai or 0) < 0 else '') + self._rp(abs(nilai or 0))
