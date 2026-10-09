from odoo import api, fields, models

from .koperasi_periode import BULAN_SINGKAT
from .koperasi_pinjaman import STATE_PINJAMAN
from .koperasi_tagihan import STATE_TAGIHAN

KOMPONEN = ('simpok', 'simwa', 'simsula', 'angsuran_pokok', 'angsuran_jasa')
JUMLAH_PERIODE = 12
JUMLAH_BARIS = 8
JUMLAH_PILIHAN_PERIODE = 36


class KoperasiDashboard(models.AbstractModel):
    """Penyedia data dashboard (client action `koperasi.dashboard`).

    Semua angka dihitung dengan hak pengguna yang memanggil, tanpa sudo:
    admin mitra otomatis hanya mendapat data mitranya (record rule), dan
    tagihan Draft tidak ikut terhitung baginya.

    Filter periode hanya berlaku untuk angka ALIRAN (tagihan, simpanan yang
    diterima, pinjaman yang dicairkan, pendapatan). Angka POSISI (anggota,
    saldo simpanan, sisa pinjaman, tunggakan, antrean) selalu keadaan saat ini.
    """
    _name = 'koperasi.dashboard'
    _description = 'Data Dashboard Koperasi'

    @api.model
    def get_data(self, mitra_id=False, periode='sekarang'):
        """periode: 'sekarang' (bulan berjalan, bawaan), 'semua', atau id koperasi.periode."""
        Mitra = self.env['koperasi.mitra']
        Periode = self.env['koperasi.periode']
        semua_mitra = Mitra.search([])
        mitra = semua_mitra.filtered(lambda m: m.id == mitra_id) if mitra_id else semua_mitra
        dm = [('mitra_id', 'in', mitra.ids)]

        sekarang = Periode._untuk_tanggal(fields.Date.context_today(self))
        self._pastikan_periode_pencairan(dm, sekarang)
        if periode == 'semua':
            p = Periode
        elif isinstance(periode, int) and not isinstance(periode, bool):
            p = Periode.browse(periode).exists() or sekarang
        else:
            p = sekarang

        baris = self._baris_tagihan(dm)
        baris_p = [b for b in baris if b['periode'] == p] if p else baris
        # Pilihan periode: sampai bulan berjalan, atau sampai periode tagihan terakhir bila lebih jauh
        batas = max([sekarang.tanggal_mulai] + [b['periode'].tanggal_mulai for b in baris])
        pilihan = Periode.search(
            [('tanggal_mulai', '<=', batas)], order='tanggal_mulai desc', limit=JUMLAH_PILIHAN_PERIODE)
        return {
            'is_operator': self.env.user.has_group('koperasi.group_koperasi_operator'),
            'mitra_list': [{'id': m.id, 'name': m.name} for m in semua_mitra],
            'mitra_id': mitra.id if mitra_id and mitra else False,
            'periode_list': [{'id': x.id, 'name': x.name} for x in pilihan | p],
            'periode': {'id': p.id or False, 'label': p.name or 'Semua Periode'},
            'simbol': self.env.company.currency_id.symbol or 'Rp',
            'ringkasan': self._ringkasan(mitra, dm, baris, baris_p, p),
            'tren': self._tren(baris, p),
            'per_mitra': self._per_mitra(mitra),
            'simpanan': self._simpanan(dm, baris_p, p),
            'pinjaman': self._pinjaman(dm, p),
            'tagihan': self._tagihan(dm, baris_p, p),
        }

    def _pastikan_periode_pencairan(self, dm, sekarang):
        """Pinjaman bisa cair pada bulan yang belum punya data periode (belum
        ada tagihan, cron belum jalan). Tanpa periodenya, bulan itu tidak bisa
        dipilih dan pencairannya hilang dari hitungan per periode."""
        Periode = self.env['koperasi.periode']
        awal = self.env['koperasi.pinjaman']._read_group(
            dm + [('tanggal_cair', '!=', False)], [], ['tanggal_cair:min'])[0][0]
        if not awal:
            return
        awal = awal.replace(day=1)
        bulan = ((sekarang.tanggal_mulai.year - awal.year) * 12
                 + sekarang.tanggal_mulai.month - awal.month)
        ada = Periode.search_count(
            [('tanggal_mulai', '>=', awal), ('tanggal_mulai', '<', sekarang.tanggal_mulai)])
        if bulan > ada:
            Periode._pastikan_ke_depan(jumlah=bulan, mulai=awal)

    # ------------------------------------------------------------------
    # Tagihan: satu query dikelompokkan per tagihan dan status bayar
    # ------------------------------------------------------------------
    def _baris_tagihan(self, dm):
        """[{tagihan, periode (record), status, simpok, ..., tunggakan}] untuk
        tagihan yang sudah ditagihkan. Komponen = nilai asli baris, tanpa
        tunggakan, supaya tunggakan yang terbawa tidak terhitung dua kali."""
        Line = self.env['koperasi.tagihan.line']
        data = Line._read_group(
            dm + [('tagihan_id.state', '!=', 'draft')],
            ['tagihan_id', 'status'],
            ['%s:sum' % k for k in KOMPONEN] + ['tunggakan:sum', '__count'])
        hasil = []
        for tagihan, status, *nilai in data:
            komponen = dict(zip(KOMPONEN + ('tunggakan', 'jumlah'), [v or 0 for v in nilai]))
            komponen.update(tagihan=tagihan, periode=tagihan.periode_id, status=status)
            komponen['simpanan'] = komponen['simpok'] + komponen['simwa'] + komponen['simsula']
            komponen['angsuran'] = komponen['angsuran_pokok'] + komponen['angsuran_jasa']
            komponen['asli'] = komponen['simpanan'] + komponen['angsuran']
            hasil.append(komponen)
        return hasil

    def _tunggakan(self, dm):
        """Sama dengan definisi di mitra/anggota: baris Belum Dibayar yang belum
        ditagih ulang (utuh) + bagian tunggakan pada baris yang belum divalidasi."""
        Line = self.env['koperasi.tagihan.line']
        belum = Line._read_group(
            dm + [('status', '=', 'belum'), ('dibawa_ke_id', '=', False)], [], ['total:sum'])[0][0]
        terbawa = Line._read_group(
            dm + [('status', '=', 'menunggu')], [], ['tunggakan:sum'])[0][0]
        return (belum or 0) + (terbawa or 0)

    def _tren(self, baris, p):
        """12 periode yang berakhir di periode terpilih (atau 12 terakhir):
        ditagihkan vs diterima, dipisah simpanan dan angsuran."""
        per = {}
        for b in baris:
            periode = b['periode']
            if p and periode.tanggal_mulai > p.tanggal_mulai:
                continue
            t = per.setdefault(periode, {
                'periode': periode.name, 'urut': periode.tanggal_mulai,
                'simpanan': 0, 'angsuran': 0, 'ditagihkan': 0, 'diterima': 0, 'belum': 0,
                'simpok': 0, 'simwa': 0, 'simsula': 0})
            t['simpanan'] += b['simpanan']
            t['angsuran'] += b['angsuran']
            t['ditagihkan'] += b['asli']
            if b['status'] == 'dibayar':
                t['diterima'] += b['asli']
                for k in ('simpok', 'simwa', 'simsula'):
                    t[k] += b[k]
            elif b['status'] == 'belum':
                t['belum'] += b['asli']
        urut = sorted(per.values(), key=lambda t: t['urut'])[-JUMLAH_PERIODE:]
        for t in urut:
            t.pop('urut')
        return urut

    # ------------------------------------------------------------------
    # Bagian-bagian dashboard
    # ------------------------------------------------------------------
    def _domain_cair(self, dm, p):
        """Pinjaman yang sudah cair; dibatasi bulan pencairan bila periode dipilih."""
        domain = dm + [('state', 'in', ('berjalan', 'lunas'))]
        if p:
            domain += [('tanggal_cair', '>=', p.tanggal_mulai), ('tanggal_cair', '<=', p.tanggal_akhir)]
        return domain

    def _ringkasan(self, mitra, dm, baris, baris_p, p):
        Anggota = self.env['koperasi.anggota']
        Pinjaman = self.env['koperasi.pinjaman']
        Tagihan = self.env['koperasi.tagihan']
        anggota = {state: n for state, n in Anggota._read_group(dm, ['state'], ['__count'])}
        pinjaman = {state: (n, sisa or 0) for state, n, sisa in Pinjaman._read_group(
            dm, ['state'], ['__count', 'sisa_pokok:sum'])}
        saldo = {k: sum(b[k] for b in baris if b['status'] == 'dibayar')
                 for k in ('simpok', 'simwa', 'simsula')}
        cair = Pinjaman._read_group(self._domain_cair(dm, p), [], ['pokok:sum'])[0][0]
        return {
            # Posisi saat ini
            'mitra': len(mitra),
            'anggota_aktif': anggota.get('aktif', 0),
            'anggota_total': sum(anggota.values()),
            'saldo_simpok': saldo['simpok'],
            'saldo_simwa': saldo['simwa'],
            'saldo_simsula': saldo['simsula'],
            'saldo_total': sum(saldo.values()),
            'pinjaman_berjalan': pinjaman.get('berjalan', (0, 0))[0],
            'pinjaman_sisa': pinjaman.get('berjalan', (0, 0))[1],
            'tunggakan': self._tunggakan(dm),
            # Aliran pada periode terpilih
            'simpanan_diterima': sum(b['simpanan'] for b in baris_p if b['status'] == 'dibayar'),
            'pinjaman_cair': cair or 0,
            # Antrean pekerjaan operator / admin mitra
            'anggota_draft': anggota.get('draft', 0),
            'anggota_menunggu': anggota.get('menunggu', 0),
            'pinjaman_diajukan': pinjaman.get('diajukan', (0, 0))[0],
            'pinjaman_disetujui': pinjaman.get('disetujui', (0, 0))[0],
            'tagihan_draft': Tagihan.search_count(dm + [('state', '=', 'draft')]),
            'tagihan_terbit': Tagihan.search_count(dm + [('state', '=', 'terbit')]),
        }

    def _per_mitra(self, mitra):
        return [{
            'id': m.id,
            'name': m.name,
            'kode': m.kode,
            'anggota': m.anggota_count,
            'simpanan': m.saldo_simpanan,
            'pinjaman_berjalan': m.pinjaman_berjalan_count,
            'pinjaman_sisa': m.pinjaman_sisa,
            'tunggakan': m.tunggakan_total,
        } for m in mitra.sorted(lambda m: -m.saldo_simpanan)]

    def _simpanan(self, dm, baris_p, p):
        """Simpanan yang diterima (dibayar) pada periode terpilih, atau seluruh saldo."""
        Line = self.env['koperasi.tagihan.line']
        dibayar = dm + [('status', '=', 'dibayar')]
        if p:
            dibayar.append(('tagihan_id.periode_id', '=', p.id))
        agregat = ['simpok:sum', 'simwa:sum', 'simsula:sum']
        per_mitra = [{
            'id': m.id, 'name': m.name,
            'simpok': pk or 0, 'simwa': w or 0, 'simsula': s or 0,
            'total': (pk or 0) + (w or 0) + (s or 0),
        } for m, pk, w, s in Line._read_group(dibayar, ['mitra_id'], agregat)]
        penabung = [{
            'id': a.id, 'name': a.name, 'mitra': a.mitra_id.name, 'nomor': a.nomor_anggota or '',
            'simsula': s or 0, 'total': (pk or 0) + (w or 0) + (s or 0),
        } for a, pk, w, s in Line._read_group(dibayar, ['anggota_id'], agregat)]
        jenis = {k: sum(b[k] for b in baris_p if b['status'] == 'dibayar')
                 for k in ('simpok', 'simwa', 'simsula')}
        Anggota = self.env['koperasi.anggota']
        ikut = Anggota.search_count(dm + [('state', '=', 'aktif'), ('simsula', '>', 0)])
        komitmen = Anggota._read_group(dm + [('state', '=', 'aktif')], [], ['simsula:sum'])[0][0]
        return {
            'simpok': jenis['simpok'],
            'simwa': jenis['simwa'],
            'simsula': jenis['simsula'],
            'total': sum(jenis.values()),
            'per_mitra': sorted(per_mitra, key=lambda r: -r['total']),
            'penabung': sorted(penabung, key=lambda r: -r['total'])[:JUMLAH_BARIS],
            'peserta_simsula': ikut,
            'simsula_bulanan': komitmen or 0,
        }

    def _pinjaman(self, dm, p):
        Pinjaman = self.env['koperasi.pinjaman']
        Angsuran = self.env['koperasi.pinjaman.angsuran']
        label = dict(STATE_PINJAMAN)
        per_state = {state: (n, pokok or 0) for state, n, pokok in Pinjaman._read_group(
            dm, ['state'], ['__count', 'pokok:sum'])}
        semua_cair = self._domain_cair(dm, False)
        # Aliran: mengikuti periode
        tersalurkan, admin, jumlah_cair = Pinjaman._read_group(
            self._domain_cair(dm, p), [], ['pokok:sum', 'biaya_admin:sum', '__count'])[0]
        dom_angsuran = [('pinjaman_id.mitra_id', 'in', dm[0][2]), ('status', '=', 'dibayar')]
        if p:
            dom_angsuran.append(('periode_id', '=', p.id))
        jasa, pokok_kembali = Angsuran._read_group(dom_angsuran, [], ['jasa:sum', 'pokok:sum'])[0]
        dom_lunas = dm + [('state', '=', 'lunas')]
        if p:
            dom_lunas += [('tanggal_lunas', '>=', p.tanggal_mulai), ('tanggal_lunas', '<=', p.tanggal_akhir)]
        # Posisi: selalu keadaan saat ini
        sisa, total_tersalurkan = Pinjaman._read_group(
            semua_cair, [], ['sisa_pokok:sum', 'pokok:sum'])[0]
        penyaluran = [{
            'label': '%s %s' % (BULAN_SINGKAT[str(bulan.month)], bulan.year),
            'pokok': pokok or 0, 'jumlah': n,
        } for bulan, pokok, n in Pinjaman._read_group(
            semua_cair + [('tanggal_cair', '!=', False)]
            + ([('tanggal_cair', '<=', p.tanggal_akhir)] if p else []),
            ['tanggal_cair:month'], ['pokok:sum', '__count'],
            order='tanggal_cair:month')][-JUMLAH_PERIODE:]

        def baris(x):
            return {
                'id': x.id, 'name': x.name, 'anggota': x.anggota_id.name, 'mitra': x.mitra_id.name,
                'produk': x.produk_id.name, 'pokok': x.pokok, 'tenor': x.tenor,
                'angsuran': x.angsuran_bulan, 'sisa': x.sisa_pokok,
                'dibayar': x.angsuran_dibayar, 'state': x.state, 'state_label': label[x.state],
            }
        return {
            'tersalurkan': tersalurkan or 0,
            'jumlah_cair': jumlah_cair,
            'pokok_kembali': pokok_kembali or 0,
            'jasa_diterima': jasa or 0,
            'admin_diterima': admin or 0,
            'pendapatan': (jasa or 0) + (admin or 0),
            'lunas': Pinjaman.search_count(dom_lunas),
            'sisa': sisa or 0,
            'total_tersalurkan': total_tersalurkan or 0,
            'berjalan': per_state.get('berjalan', (0, 0))[0],
            'per_state': [
                {'state': s, 'label': label[s], 'jumlah': per_state[s][0], 'pokok': per_state[s][1]}
                for s, _l in STATE_PINJAMAN if s in per_state],
            'penyaluran': penyaluran,
            'pengajuan': [baris(x) for x in Pinjaman.search(
                dm + [('state', 'in', ('diajukan', 'disetujui'))], order='id', limit=JUMLAH_BARIS)],
            'terbesar': [baris(x) for x in Pinjaman.search(
                dm + [('state', '=', 'berjalan')], order='sisa_pokok desc', limit=JUMLAH_BARIS)],
        }

    def _tagihan(self, dm, baris_p, p):
        Tagihan = self.env['koperasi.tagihan']
        Line = self.env['koperasi.tagihan.line']
        label = dict(STATE_TAGIHAN)
        jumlah = {s: sum(b['asli'] for b in baris_p if b['status'] == s)
                  for s in ('dibayar', 'belum', 'menunggu')}
        tervalidasi = jumlah['dibayar'] + jumlah['belum']
        menunggak = {}
        for anggota, total in Line._read_group(
                dm + [('status', '=', 'belum'), ('dibawa_ke_id', '=', False)],
                ['anggota_id'], ['total:sum']):
            menunggak[anggota] = menunggak.get(anggota, 0) + (total or 0)
        for anggota, total in Line._read_group(
                dm + [('status', '=', 'menunggu'), ('tunggakan', '>', 0)],
                ['anggota_id'], ['tunggakan:sum']):
            menunggak[anggota] = menunggak.get(anggota, 0) + (total or 0)
        dom_tagihan = dm + ([('periode_id', '=', p.id)] if p else [])
        return {
            # Aliran pada periode terpilih
            'ditagihkan': sum(jumlah.values()),
            'diterima': jumlah['dibayar'],
            'belum': jumlah['belum'],
            'menunggu': jumlah['menunggu'],
            # dari tagihan yang sudah divalidasi, berapa persen yang dibayar
            'kolektibilitas': round(jumlah['dibayar'] * 100 / tervalidasi, 1) if tervalidasi else None,
            'terbaru': [{
                'id': t.id, 'name': t.name, 'mitra': t.mitra_id.name, 'periode': t.periode_id.name,
                'anggota': t.jumlah_anggota, 'total': t.total, 'dibayar': t.total_dibayar,
                'belum': t.total_belum, 'menunggu': t.jumlah_menunggu,
                'state': t.state, 'state_label': label[t.state],
            } for t in Tagihan.search(dom_tagihan, order='periode desc, id desc', limit=JUMLAH_BARIS)],
            # Posisi saat ini
            'tunggakan': self._tunggakan(dm),
            'menunggak': [{
                'id': a.id, 'name': a.name, 'mitra': a.mitra_id.name,
                'nomor': a.nomor_anggota or '', 'tunggakan': total,
            } for a, total in sorted(menunggak.items(), key=lambda x: -x[1])[:JUMLAH_BARIS]],
        }
