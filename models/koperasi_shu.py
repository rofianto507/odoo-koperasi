"""Tutup buku tahunan: perhitungan laba bersih dan pembagiannya, serta jurnal penutup.

Model usahanya: pengelola (pemodal sekaligus pemilik platform) menanam modal yang disalurkan
sebagai pinjaman kepada pekerja perusahaan mitra. Laba setahun = pendapatan − beban dari jurnal
terposting, dan pada dasarnya milik pemodal. Pembagiannya mengikuti persentase di Pengaturan:

- Imbal hasil pemodal : bagian laba yang dibayarkan kepada pemodal.
- Laba ditahan        : tetap di usaha sebagai ekuitas (juga menampung sisa pembulatan dan rugi).
- Imbalan anggota     : dibagi ke anggota sebanding saldo simpanannya pada akhir tahun.
- Komisi mitra        : dibagi ke mitra sebanding pendapatan (jasa pinjaman + biaya administrasi)
                        yang dihasilkan anggotanya selama tahun itu.

Alur: Draft (bisa dihitung kapan saja sebagai simulasi) -> Ditetapkan (semua periode tahun itu
sudah ditutup; jurnal penutup memindahkan pendapatan dan beban ke pos-pos di atas) -> Dibayar
(imbal hasil, imbalan anggota, dan komisi mitra dibayarkan dari Bank).

Nama teknis model tetap `koperasi.shu` (dari rancangan awal berbasis Sisa Hasil Usaha).
"""
from collections import defaultdict
from datetime import date

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

STATE_SHU = [('draft', 'Draft'), ('ditetapkan', 'Ditetapkan'), ('dibayar', 'Dibayar')]
# (kunci, label, kunci akun sistem tujuan pada jurnal penutup)
POS_LABA = [
    ('pemodal', 'Imbal Hasil Pemodal', 'utang_pemodal'),
    ('ditahan', 'Laba Ditahan', 'cadangan'),
    ('anggota', 'Imbalan Simpanan Anggota', 'shu_anggota'),
    ('mitra', 'Komisi Mitra', 'utang_mitra'),
]
# Pos yang dibayarkan keluar (kewajiban sampai dibayar); Laba Ditahan tetap di ekuitas
POS_DIBAYAR = ('pemodal', 'anggota', 'mitra')
BAWAAN = {'pemodal': 60.0, 'ditahan': 20.0, 'anggota': 10.0, 'mitra': 10.0}
LABA_RUGI = ('pendapatan', 'beban')
# Hanya ditulis oleh tombol Hitung / Tetapkan / Bayar
FIELD_HASIL = {
    'state', 'tanggal_hitung', 'total_pendapatan', 'total_beban', 'shu', 'line_ids', 'mitra_ids',
    'tanggal_tetap', 'jurnal_id', 'tanggal_bayar', 'jurnal_bayar_id', 'modal_penyertaan',
} | {'nominal_' + k for k, _l, _a in POS_LABA}


class KoperasiPengaturan(models.Model):
    _inherit = 'koperasi.pengaturan'

    laba_pemodal = fields.Float(
        'Imbal Hasil Pemodal (%)', digits=(5, 2), default=BAWAAN['pemodal'], tracking=True,
        help='Bagian laba bersih yang dibayarkan kepada pemodal.')
    laba_ditahan = fields.Float(
        'Laba Ditahan (%)', digits=(5, 2), default=BAWAAN['ditahan'], tracking=True,
        help='Bagian laba yang tetap di usaha untuk memperbesar dana pinjaman.')
    laba_anggota = fields.Float(
        'Imbalan Simpanan Anggota (%)', digits=(5, 2), default=BAWAAN['anggota'], tracking=True,
        help='Dibagi ke anggota sebanding saldo simpanannya pada akhir tahun. Isi 0 bila tidak ada.')
    laba_mitra = fields.Float(
        'Komisi Mitra (%)', digits=(5, 2), default=BAWAAN['mitra'], tracking=True,
        help='Dibagi ke mitra sebanding pendapatan yang dihasilkan anggotanya. Isi 0 bila tidak ada.')

    @api.constrains('laba_pemodal', 'laba_ditahan', 'laba_anggota', 'laba_mitra')
    def _cek_persen_laba(self):
        for r in self:
            r._cek_jumlah_persen([r['laba_' + k] for k, _l, _a in POS_LABA])

    @api.model
    def _cek_jumlah_persen(self, persen):
        if any(p < 0 for p in persen):
            raise ValidationError(_('Persentase pembagian laba tidak boleh negatif.'))
        if abs(sum(persen) - 100) > 0.005:
            raise ValidationError(_(
                'Jumlah persentase pembagian laba harus 100%% (sekarang %s%%).',
                ('%.2f' % sum(persen)).rstrip('0').rstrip('.')))


class KoperasiShu(models.Model):
    _name = 'koperasi.shu'
    _description = 'Tutup Buku Tahunan dan Pembagian Laba'
    _inherit = ['mail.thread', 'koperasi.mata.uang.mixin']
    _order = 'tahun desc'

    name = fields.Char('Nama', compute='_compute_name', store=True)
    tahun = fields.Integer(
        'Tahun Buku', required=True, default=lambda self: fields.Date.context_today(self).year)
    state = fields.Selection(
        STATE_SHU, string='Status', default='draft', required=True, readonly=True,
        copy=False, tracking=True)
    tanggal_hitung = fields.Datetime('Terakhir Dihitung', readonly=True, copy=False)
    total_pendapatan = fields.Monetary('Total Pendapatan', readonly=True, copy=False)
    total_beban = fields.Monetary('Total Beban', readonly=True, copy=False)
    shu = fields.Monetary('Laba Bersih', readonly=True, copy=False, tracking=True)

    # Ukuran untuk pemodal
    modal_penyertaan = fields.Monetary(
        'Modal Penyertaan', readonly=True, copy=False,
        help='Saldo akun Modal Penyertaan pada akhir tahun buku.')
    laba_atas_modal = fields.Float(
        'Laba terhadap Modal (%)', digits=(6, 2), compute='_compute_rasio',
        help='Laba bersih dibagi modal penyertaan. Untuk tahun yang belum selesai, ini angka '
             'sampai hari ini, belum disetahunkan.')
    imbal_atas_modal = fields.Float(
        'Imbal Hasil terhadap Modal (%)', digits=(6, 2), compute='_compute_rasio',
        help='Imbal hasil pemodal dibagi modal penyertaan.')

    persen_pemodal = fields.Float('Imbal Hasil Pemodal (%)', digits=(5, 2))
    persen_ditahan = fields.Float('Laba Ditahan (%)', digits=(5, 2))
    persen_anggota = fields.Float('Imbalan Simpanan Anggota (%)', digits=(5, 2))
    persen_mitra = fields.Float('Komisi Mitra (%)', digits=(5, 2))
    nominal_pemodal = fields.Monetary('Imbal Hasil Pemodal', readonly=True, copy=False)
    nominal_ditahan = fields.Monetary('Laba Ditahan', readonly=True, copy=False)
    nominal_anggota = fields.Monetary('Imbalan Simpanan Anggota', readonly=True, copy=False)
    nominal_mitra = fields.Monetary('Komisi Mitra', readonly=True, copy=False)
    nominal_dibayar = fields.Monetary(
        'Dibayarkan Keluar', compute='_compute_rasio',
        help='Imbal hasil pemodal + imbalan anggota + komisi mitra.')

    line_ids = fields.One2many('koperasi.shu.line', 'shu_id', string='Imbalan per Anggota', readonly=True)
    mitra_ids = fields.One2many('koperasi.shu.mitra', 'shu_id', string='Komisi per Mitra', readonly=True)
    jumlah_anggota = fields.Integer('Anggota Penerima', compute='_compute_jumlah')
    jumlah_mitra = fields.Integer('Mitra Penerima', compute='_compute_jumlah')
    tanggal_tetap = fields.Date('Tanggal Ditetapkan', readonly=True, copy=False)
    jurnal_id = fields.Many2one('koperasi.jurnal', string='Jurnal Penutup', readonly=True, copy=False)
    tanggal_bayar = fields.Date('Tanggal Dibayar', readonly=True, copy=False)
    jurnal_bayar_id = fields.Many2one(
        'koperasi.jurnal', string='Jurnal Pembayaran', readonly=True, copy=False)
    catatan = fields.Text('Catatan')

    _tahun_uniq = models.Constraint('UNIQUE(tahun)', 'Tutup buku untuk tahun tersebut sudah ada.')
    _tahun_wajar = models.Constraint('CHECK(tahun BETWEEN 2000 AND 2100)', 'Tahun buku tidak valid.')

    @api.depends('tahun')
    def _compute_name(self):
        for r in self:
            r.name = _('Laba %s', r.tahun)

    @api.depends('line_ids', 'mitra_ids')
    def _compute_jumlah(self):
        for r in self:
            r.jumlah_anggota = len(r.line_ids)
            r.jumlah_mitra = len(r.mitra_ids)

    @api.depends('shu', 'modal_penyertaan', 'nominal_pemodal', 'nominal_anggota', 'nominal_mitra')
    def _compute_rasio(self):
        for r in self:
            modal = r.modal_penyertaan
            r.laba_atas_modal = r.shu * 100 / modal if modal > 0 else 0
            r.imbal_atas_modal = r.nominal_pemodal * 100 / modal if modal > 0 else 0
            r.nominal_dibayar = sum(r['nominal_' + k] for k in POS_DIBAYAR)

    @api.constrains('persen_pemodal', 'persen_ditahan', 'persen_anggota', 'persen_mitra')
    def _cek_persen(self):
        for r in self:
            self.env['koperasi.pengaturan']._cek_jumlah_persen([r['persen_' + k] for k, _l, _a in POS_LABA])

    @api.model_create_multi
    def create(self, vals_list):
        cfg = self.env['koperasi.pengaturan']._get()
        for vals in vals_list:
            for k, _l, _a in POS_LABA:
                vals.setdefault('persen_' + k, cfg['laba_' + k])
        return super().create(vals_list)

    def write(self, vals):
        if any(f.startswith('persen_') or f == 'tahun' for f in vals) \
                and any(r.state != 'draft' for r in self):
            raise UserError(_('Tahun dan persentase hanya dapat diubah saat masih Draft.'))
        if FIELD_HASIL & vals.keys() and not self.env.context.get('koperasi_shu'):
            raise UserError(_('Hasil perhitungan dan status tutup buku diatur oleh sistem.'))
        return super().write(vals)

    def _tulis(self, vals):
        return self.with_context(koperasi_shu=True).write(vals)

    def unlink(self):
        if any(r.state != 'draft' for r in self):
            raise UserError(_('Hanya tutup buku berstatus Draft yang dapat dihapus.'))
        return super().unlink()

    def _cek_operator(self):
        if not self.env.user.has_group('koperasi.group_koperasi_operator'):
            raise UserError(_('Hanya operator pusat yang dapat mengelola tutup buku.'))

    # ------------------------------------------------------------------
    # Perhitungan
    # ------------------------------------------------------------------
    def _rentang(self):
        self.ensure_one()
        return date(self.tahun, 1, 1), date(self.tahun, 12, 31)

    def _laba_rugi(self):
        """{akun: saldo debit − kredit} akun pendapatan dan beban setahun, tanpa jurnal penutup."""
        self.ensure_one()
        awal, akhir = self._rentang()
        data = self.env['koperasi.jurnal.line'].sudo()._read_group([
            ('state', '=', 'posted'), ('sumber', '!=', 'penutup'), ('jenis_akun', 'in', LABA_RUGI),
            ('tanggal', '>=', awal), ('tanggal', '<=', akhir)], ['akun_id'], ['debit:sum', 'kredit:sum'])
        return {akun: (d or 0) - (k or 0) for akun, d, k in data}

    def _modal(self):
        """Saldo akun Modal Penyertaan sampai akhir tahun buku."""
        self.ensure_one()
        akun = self.env['koperasi.akun']._sistem('modal')
        d, k = self.env['koperasi.jurnal.line'].sudo()._read_group([
            ('state', '=', 'posted'), ('akun_id', '=', akun.id), ('tanggal', '<=', self._rentang()[1])],
            [], ['debit:sum', 'kredit:sum'])[0]
        return (k or 0) - (d or 0)

    def _dasar_anggota(self):
        """{anggota_id: saldo simpanan (pokok + wajib + sukarela) pada akhir tahun buku}"""
        self.ensure_one()
        akhir = self._rentang()[1]
        agregat = ['simpok:sum', 'simwa:sum', 'simsula:sum']
        simpanan = defaultdict(float)
        for a, *nilai in self.env['koperasi.tagihan.line'].sudo()._read_group(
                [('status', '=', 'dibayar'), ('tanggal_akui', '<=', akhir)], ['anggota_id'], agregat):
            simpanan[a.id] += sum(v or 0 for v in nilai)
        for a, *nilai in self.env['koperasi.penarikan'].sudo()._read_group(
                [('state', '=', 'dibayar'), ('tanggal_bayar', '<=', akhir)], ['anggota_id'], agregat):
            simpanan[a.id] -= sum(v or 0 for v in nilai)
        return {k: v for k, v in simpanan.items() if v > 0.5}

    def _dasar_mitra(self):
        """{mitra_id: jasa pinjaman + biaya administrasi dari anggota mitra itu selama tahun buku}"""
        self.ensure_one()
        awal, akhir = self._rentang()
        pendapatan = defaultdict(float)
        for m, j in self.env['koperasi.pinjaman.angsuran'].sudo()._read_group(
                [('status', '=', 'dibayar'), ('tanggal_akui', '>=', awal), ('tanggal_akui', '<=', akhir)],
                ['mitra_id'], ['jasa:sum']):
            pendapatan[m.id] += j or 0
        for m, adm in self.env['koperasi.pinjaman'].sudo()._read_group(
                [('tanggal_cair', '>=', awal), ('tanggal_cair', '<=', akhir)],
                ['mitra_id'], ['biaya_admin:sum']):
            pendapatan[m.id] += adm or 0
        return {k: v for k, v in pendapatan.items() if k and v > 0.5}

    @staticmethod
    def _bagi(nominal, dasar):
        """Bagi nominal sebanding dasar, dibulatkan ke bawah per rupiah."""
        total = sum(dasar.values())
        if nominal <= 0 or total <= 0:
            return {}
        return {k: int(nominal * v / total) for k, v in dasar.items()}

    def action_hitung(self):
        """Hitung laba dan pembagiannya dari jurnal terposting. Boleh diulang selama Draft."""
        self._cek_operator()
        for r in self:
            if r.state != 'draft':
                raise UserError(_('Perhitungan hanya dapat diulang saat masih Draft.'))
            saldo = r._laba_rugi()
            pendapatan = sum(-v for a, v in saldo.items() if a.jenis == 'pendapatan')
            beban = sum(v for a, v in saldo.items() if a.jenis == 'beban')
            laba = pendapatan - beban
            nominal = {k: (round(laba * r['persen_' + k] / 100) if laba > 0 else 0) for k, _l, _a in POS_LABA}
            simpanan, dari_mitra = r._dasar_anggota(), r._dasar_mitra()
            imbalan = r._bagi(nominal['anggota'], simpanan)
            komisi = r._bagi(nominal['mitra'], dari_mitra)
            # yang benar-benar terbagi; sisa pembulatan, pos tanpa penerima, dan seluruh rugi
            # masuk Laba Ditahan
            nominal['anggota'] = sum(imbalan.values())
            nominal['mitra'] = sum(komisi.values())
            nominal['ditahan'] = laba - sum(v for k, v in nominal.items() if k != 'ditahan')
            r._tulis(dict(
                {'nominal_' + k: v for k, v in nominal.items()},
                total_pendapatan=pendapatan, total_beban=beban, shu=laba, modal_penyertaan=r._modal(),
                tanggal_hitung=fields.Datetime.now(),
                line_ids=[fields.Command.clear()] + [
                    fields.Command.create({'anggota_id': a, 'dasar_simpanan': simpanan[a], 'total': v})
                    for a, v in sorted(imbalan.items()) if v > 0],
                mitra_ids=[fields.Command.clear()] + [
                    fields.Command.create({'mitra_id': m, 'dasar_pendapatan': dari_mitra[m], 'komisi': v})
                    for m, v in sorted(komisi.items()) if v > 0]))
        return True

    # ------------------------------------------------------------------
    # Penetapan (jurnal penutup) dan pembayaran
    # ------------------------------------------------------------------
    def _cek_periode_ditutup(self):
        self.ensure_one()
        awal, akhir = self._rentang()
        Periode = self.env['koperasi.periode'].sudo()
        Periode._pastikan_ke_depan(jumlah=12, mulai=awal)
        terbuka = Periode.search([
            ('tanggal_mulai', '>=', awal), ('tanggal_mulai', '<=', akhir), ('state', '!=', 'ditutup')],
            order='tanggal_mulai')
        if terbuka:
            raise UserError(_(
                'Laba %(tahun)s baru dapat ditetapkan setelah semua periode tahun itu ditutup. '
                'Masih terbuka: %(periode)s.', tahun=self.tahun,
                periode=', '.join(terbuka.mapped('name'))))

    def action_tetapkan(self):
        """Tetapkan pembagian laba dan buat jurnal penutup per 31 Desember."""
        self._cek_operator()
        Akun = self.env['koperasi.akun']
        for r in self:
            if r.state != 'draft':
                raise UserError(_('Hanya tutup buku berstatus Draft yang dapat ditetapkan.'))
            r._cek_periode_ditutup()
            r.action_hitung()  # angka final dari jurnal yang sudah terkunci
            saldo = r._laba_rugi()
            mata_uang = self.env.company.currency_id
            baris = []
            for akun in sorted(saldo, key=lambda a: a.kode):
                v = saldo[akun]
                if not mata_uang.is_zero(v):
                    # nolkan akun: saldo debit (beban) dikredit, saldo kredit (pendapatan) didebit
                    baris.append((akun, -v if v < 0 else 0, v if v > 0 else 0))
            if not baris:
                raise UserError(_('Tidak ada pendapatan atau beban pada tahun %s untuk ditutup.', r.tahun))
            for k, _l, akun_kunci in POS_LABA:
                nilai = r['nominal_' + k]
                # rugi (laba ditahan negatif) didebit ke Laba Ditahan
                baris.append((Akun._sistem(akun_kunci), -nilai if nilai < 0 else 0, nilai if nilai > 0 else 0))
            jurnal = self.env['koperasi.jurnal']._buat_otomatis({
                'tanggal': r._rentang()[1],
                'keterangan': _('Jurnal penutup dan pembagian laba tahun %s', r.tahun),
                'sumber': 'penutup',
                'shu_id': r.id,
            }, baris)
            r._tulis({'state': 'ditetapkan', 'tanggal_tetap': fields.Date.context_today(r), 'jurnal_id': jurnal.id})
        return True

    def action_draft(self):
        """Batalkan penetapan (sebelum dibayar): jurnal penutup dihapus."""
        self._cek_operator()
        for r in self:
            if r.state != 'ditetapkan':
                raise UserError(_('Hanya penetapan yang belum dibayar yang dapat dibatalkan.'))
            jurnal = r.jurnal_id.sudo().with_context(koperasi_jurnal=True)
            r._tulis({'state': 'draft', 'tanggal_tetap': False, 'jurnal_id': False})
            jurnal.unlink()
        return True

    def action_bayar(self):
        """Catat pembayaran hari ini untuk imbal hasil pemodal, imbalan anggota, dan komisi mitra."""
        self._cek_operator()
        Akun = self.env['koperasi.akun']
        hari_ini = fields.Date.context_today(self)
        akun_pos = {k: a for k, _l, a in POS_LABA}
        for r in self:
            if r.state != 'ditetapkan':
                raise UserError(_('Pembayaran hanya untuk laba yang sudah ditetapkan.'))
            if r.nominal_dibayar <= 0:
                raise UserError(_('Tidak ada bagian laba yang perlu dibayarkan.'))
            baris = [(Akun._sistem(akun_pos[k]), r['nominal_' + k], 0) for k in POS_DIBAYAR]
            baris.append((Akun._sistem('bank'), 0, r.nominal_dibayar))
            jurnal = self.env['koperasi.jurnal']._buat_otomatis({
                'tanggal': hari_ini,
                'keterangan': _('Pembayaran pembagian laba tahun %s', r.tahun),
                'sumber': 'shu',
                'shu_id': r.id,
            }, baris)
            r._tulis({'state': 'dibayar', 'tanggal_bayar': hari_ini, 'jurnal_bayar_id': jurnal.id})
        return True

    def action_cetak(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/koperasi/laporan/pdf?jenis=shu&shu=%d' % self.id,
            'target': 'new',
        }

    def action_excel(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/koperasi/laporan/xlsx?jenis=shu&shu=%d' % self.id,
            'target': 'self',
        }


class KoperasiShuLine(models.Model):
    _name = 'koperasi.shu.line'
    _description = 'Imbalan Simpanan per Anggota'
    _order = 'total desc, id'
    _rec_name = 'anggota_id'

    shu_id = fields.Many2one('koperasi.shu', string='Tutup Buku', required=True, index=True, ondelete='cascade')
    currency_id = fields.Many2one(related='shu_id.currency_id')
    anggota_id = fields.Many2one(
        'koperasi.anggota', string='Anggota', required=True, index=True, ondelete='restrict')
    mitra_id = fields.Many2one(related='anggota_id.mitra_id', store=True, index=True)
    dasar_simpanan = fields.Monetary('Saldo Simpanan', help='Pokok + wajib + sukarela pada akhir tahun buku.')
    total = fields.Monetary('Imbalan')


class KoperasiShuMitra(models.Model):
    _name = 'koperasi.shu.mitra'
    _description = 'Komisi per Mitra'
    _order = 'komisi desc, id'
    _rec_name = 'mitra_id'

    shu_id = fields.Many2one('koperasi.shu', string='Tutup Buku', required=True, index=True, ondelete='cascade')
    currency_id = fields.Many2one(related='shu_id.currency_id')
    mitra_id = fields.Many2one('koperasi.mitra', string='Mitra', required=True, index=True, ondelete='restrict')
    dasar_pendapatan = fields.Monetary(
        'Pendapatan dari Mitra', help='Jasa pinjaman + biaya administrasi dari anggota mitra ini selama tahun buku.')
    komisi = fields.Monetary('Komisi')


class KoperasiJurnal(models.Model):
    _inherit = 'koperasi.jurnal'

    shu_id = fields.Many2one(
        'koperasi.shu', string='Tutup Buku', readonly=True, index=True, ondelete='restrict')


class KoperasiAkun(models.Model):
    _inherit = 'koperasi.akun'

    @api.model
    def _hapus_akun_usang(self):
        """Dipanggil di awal pemuatan data: buang akun pos pembagian versi koperasi (dana
        pengurus, pendidikan, sosial) yang tidak dipakai lagi, selama belum pernah dijurnal."""
        Line = self.env['koperasi.jurnal.line'].sudo()
        for xmlid in ('akun_dana_pengurus', 'akun_dana_pendidikan', 'akun_dana_sosial'):
            akun = self.env.ref('koperasi.' + xmlid, raise_if_not_found=False)
            if not akun:
                continue
            akun = akun.sudo()
            if Line.search_count([('akun_id', '=', akun.id)], limit=1):
                akun.write({'sistem': False, 'active': False})
            else:
                akun.sistem = False
                akun.unlink()

    @api.model
    def _sesuaikan_bagan_akun(self):
        """Dipanggil tiap pembaruan: sesuaikan akun bawaan lama dengan model pemodal. Simpanan
        pokok dan wajib adalah titipan anggota (kewajiban), bukan ekuitas. Hanya mengubah akun
        yang masih bernilai bawaan lama, jadi perubahan pengguna tidak ditimpa."""
        lama = {
            'akun_simpok': ({'kode': '3101', 'jenis': 'ekuitas'},
                            {'kode': '2102', 'name': 'Simpanan Pokok Anggota', 'jenis': 'kewajiban'}),
            'akun_simwa': ({'kode': '3102', 'jenis': 'ekuitas'},
                           {'kode': '2103', 'name': 'Simpanan Wajib Anggota', 'jenis': 'kewajiban'}),
            'akun_shu_anggota': ({'name': 'SHU Bagian Anggota'},
                                 {'name': 'Imbalan Anggota Terutang',
                                  'keterangan': 'Imbalan simpanan yang sudah ditetapkan tetapi belum dibayarkan.'}),
            'akun_cadangan': ({'name': 'Dana Cadangan'},
                              {'name': 'Laba Ditahan',
                               'keterangan': 'Bagian laba yang tetap di usaha; rugi tahun buku juga dibebankan ke sini.'}),
        }
        for xmlid, (syarat, baru) in lama.items():
            akun = self.env.ref('koperasi.' + xmlid, raise_if_not_found=False)
            if akun and all(akun[f] == v for f, v in syarat.items()):
                akun.sudo().write(baru)
