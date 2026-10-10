"""Tutup buku tahunan: perhitungan dan pembagian Sisa Hasil Usaha (SHU), serta jurnal penutup.

SHU setahun = pendapatan − beban dari jurnal terposting. SHU dibagi menurut persentase di
Pengaturan: dana cadangan, jasa modal (sebanding simpanan pokok + wajib anggota pada akhir
tahun), jasa usaha (sebanding jasa pinjaman + biaya administrasi yang dibayar anggota selama
tahun itu), dana pengurus dan karyawan, dana pendidikan, dan dana sosial.

Alur: Draft (bisa dihitung kapan saja sebagai simulasi) -> Ditetapkan (semua periode tahun itu
sudah ditutup; jurnal penutup memindahkan pendapatan dan beban ke ekuitas/kewajiban) ->
Dibayar (bagian anggota dibayarkan: Dr SHU Bagian Anggota / Cr Bank).
"""
from collections import defaultdict
from datetime import date

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

STATE_SHU = [('draft', 'Draft'), ('ditetapkan', 'Ditetapkan'), ('dibayar', 'Dibayar')]
# (field persentase, label, kunci akun sistem tujuan)
POS_SHU = [
    ('cadangan', 'Dana Cadangan', 'cadangan'),
    ('jasa_modal', 'Jasa Modal Anggota', 'shu_anggota'),
    ('jasa_usaha', 'Jasa Usaha Anggota', 'shu_anggota'),
    ('pengurus', 'Dana Pengurus dan Karyawan', 'dana_pengurus'),
    ('pendidikan', 'Dana Pendidikan', 'dana_pendidikan'),
    ('sosial', 'Dana Sosial', 'dana_sosial'),
]
LABA_RUGI = ('pendapatan', 'beban')
# Hanya ditulis oleh tombol Hitung / Tetapkan / Bayar
FIELD_HASIL = {
    'state', 'tanggal_hitung', 'total_pendapatan', 'total_beban', 'shu', 'line_ids', 'tanggal_tetap',
    'jurnal_id', 'tanggal_bayar', 'jurnal_bayar_id',
} | {'nominal_' + k for k in ('cadangan', 'jasa_modal', 'jasa_usaha', 'pengurus', 'pendidikan', 'sosial')}


class KoperasiPengaturan(models.Model):
    _inherit = 'koperasi.pengaturan'

    shu_cadangan = fields.Float('Dana Cadangan (%)', digits=(5, 2), default=30.0, tracking=True)
    shu_jasa_modal = fields.Float(
        'Jasa Modal Anggota (%)', digits=(5, 2), default=25.0, tracking=True,
        help='Dibagi ke anggota sebanding simpanan pokok dan wajibnya pada akhir tahun.')
    shu_jasa_usaha = fields.Float(
        'Jasa Usaha Anggota (%)', digits=(5, 2), default=25.0, tracking=True,
        help='Dibagi ke anggota sebanding jasa pinjaman dan biaya administrasi yang dibayarnya.')
    shu_pengurus = fields.Float('Dana Pengurus dan Karyawan (%)', digits=(5, 2), default=10.0, tracking=True)
    shu_pendidikan = fields.Float('Dana Pendidikan (%)', digits=(5, 2), default=5.0, tracking=True)
    shu_sosial = fields.Float('Dana Sosial (%)', digits=(5, 2), default=5.0, tracking=True)

    @api.constrains('shu_cadangan', 'shu_jasa_modal', 'shu_jasa_usaha', 'shu_pengurus',
                    'shu_pendidikan', 'shu_sosial')
    def _cek_persen_shu(self):
        for r in self:
            r._cek_jumlah_persen([r['shu_' + k] for k, _l, _a in POS_SHU])

    @api.model
    def _cek_jumlah_persen(self, persen):
        if any(p < 0 for p in persen):
            raise ValidationError(_('Persentase pembagian SHU tidak boleh negatif.'))
        if abs(sum(persen) - 100) > 0.005:
            raise ValidationError(_(
                'Jumlah persentase pembagian SHU harus 100%% (sekarang %s%%).',
                ('%.2f' % sum(persen)).rstrip('0').rstrip('.')))


class KoperasiShu(models.Model):
    _name = 'koperasi.shu'
    _description = 'Tutup Buku Tahunan dan Pembagian SHU'
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
    shu = fields.Monetary('Sisa Hasil Usaha', readonly=True, copy=False, tracking=True)

    persen_cadangan = fields.Float('Dana Cadangan (%)', digits=(5, 2))
    persen_jasa_modal = fields.Float('Jasa Modal Anggota (%)', digits=(5, 2))
    persen_jasa_usaha = fields.Float('Jasa Usaha Anggota (%)', digits=(5, 2))
    persen_pengurus = fields.Float('Dana Pengurus dan Karyawan (%)', digits=(5, 2))
    persen_pendidikan = fields.Float('Dana Pendidikan (%)', digits=(5, 2))
    persen_sosial = fields.Float('Dana Sosial (%)', digits=(5, 2))
    nominal_cadangan = fields.Monetary('Dana Cadangan', readonly=True, copy=False)
    nominal_jasa_modal = fields.Monetary('Jasa Modal Anggota', readonly=True, copy=False)
    nominal_jasa_usaha = fields.Monetary('Jasa Usaha Anggota', readonly=True, copy=False)
    nominal_pengurus = fields.Monetary('Dana Pengurus dan Karyawan', readonly=True, copy=False)
    nominal_pendidikan = fields.Monetary('Dana Pendidikan', readonly=True, copy=False)
    nominal_sosial = fields.Monetary('Dana Sosial', readonly=True, copy=False)
    nominal_anggota = fields.Monetary(
        'Bagian Anggota', compute='_compute_nominal_anggota', store=True,
        help='Jasa modal + jasa usaha yang dibagikan ke anggota.')

    line_ids = fields.One2many('koperasi.shu.line', 'shu_id', string='Bagian per Anggota', readonly=True)
    jumlah_anggota = fields.Integer('Anggota Penerima', compute='_compute_nominal_anggota', store=True)
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
            r.name = _('SHU %s', r.tahun)

    @api.depends('line_ids.total')
    def _compute_nominal_anggota(self):
        for r in self:
            r.nominal_anggota = sum(r.line_ids.mapped('total'))
            r.jumlah_anggota = len(r.line_ids)

    @api.constrains('persen_cadangan', 'persen_jasa_modal', 'persen_jasa_usaha', 'persen_pengurus',
                    'persen_pendidikan', 'persen_sosial')
    def _cek_persen(self):
        for r in self:
            self.env['koperasi.pengaturan']._cek_jumlah_persen([r['persen_' + k] for k, _l, _a in POS_SHU])

    @api.model_create_multi
    def create(self, vals_list):
        cfg = self.env['koperasi.pengaturan']._get()
        for vals in vals_list:
            for k, _l, _a in POS_SHU:
                vals.setdefault('persen_' + k, cfg['shu_' + k])
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

    def _dasar_anggota(self):
        """({anggota_id: simpanan pokok + wajib akhir tahun}, {anggota_id: jasa + biaya admin setahun})"""
        self.ensure_one()
        awal, akhir = self._rentang()
        simpanan, jasa = defaultdict(float), defaultdict(float)
        for a, pk, w in self.env['koperasi.tagihan.line'].sudo()._read_group(
                [('status', '=', 'dibayar'), ('tanggal_akui', '<=', akhir)],
                ['anggota_id'], ['simpok:sum', 'simwa:sum']):
            simpanan[a.id] += (pk or 0) + (w or 0)
        for a, pk, w in self.env['koperasi.penarikan'].sudo()._read_group(
                [('state', '=', 'dibayar'), ('tanggal_bayar', '<=', akhir)],
                ['anggota_id'], ['simpok:sum', 'simwa:sum']):
            simpanan[a.id] -= (pk or 0) + (w or 0)
        for a, j in self.env['koperasi.pinjaman.angsuran'].sudo()._read_group(
                [('status', '=', 'dibayar'), ('tanggal_akui', '>=', awal), ('tanggal_akui', '<=', akhir)],
                ['anggota_id'], ['jasa:sum']):
            jasa[a.id] += j or 0
        for a, adm in self.env['koperasi.pinjaman'].sudo()._read_group(
                [('tanggal_cair', '>=', awal), ('tanggal_cair', '<=', akhir)],
                ['anggota_id'], ['biaya_admin:sum']):
            jasa[a.id] += adm or 0
        return ({k: v for k, v in simpanan.items() if v > 0.5}, {k: v for k, v in jasa.items() if v > 0.5})

    @staticmethod
    def _bagi(nominal, dasar):
        """Bagi nominal sebanding dasar, dibulatkan ke bawah per rupiah."""
        total = sum(dasar.values())
        if nominal <= 0 or total <= 0:
            return {}
        return {k: int(nominal * v / total) for k, v in dasar.items()}

    def action_hitung(self):
        """Hitung SHU dan pembagiannya dari jurnal terposting. Boleh diulang selama Draft."""
        self._cek_operator()
        for r in self:
            if r.state != 'draft':
                raise UserError(_('Perhitungan hanya dapat diulang saat masih Draft.'))
            saldo = r._laba_rugi()
            pendapatan = sum(-v for a, v in saldo.items() if a.jenis == 'pendapatan')
            beban = sum(v for a, v in saldo.items() if a.jenis == 'beban')
            shu = pendapatan - beban
            nominal = {k: (round(shu * r['persen_' + k] / 100) if shu > 0 else 0) for k, _l, _a in POS_SHU}
            simpanan, jasa = r._dasar_anggota()
            modal = r._bagi(nominal['jasa_modal'], simpanan)
            usaha = r._bagi(nominal['jasa_usaha'], jasa)
            baris = []
            for anggota_id in sorted(set(modal) | set(usaha)):
                if modal.get(anggota_id, 0) + usaha.get(anggota_id, 0) <= 0:
                    continue
                baris.append(fields.Command.create({
                    'anggota_id': anggota_id,
                    'dasar_simpanan': simpanan.get(anggota_id, 0), 'dasar_jasa': jasa.get(anggota_id, 0),
                    'jasa_modal': modal.get(anggota_id, 0), 'jasa_usaha': usaha.get(anggota_id, 0),
                }))
            # yang benar-benar terbagi ke anggota; sisa pembulatan (atau bagian tanpa penerima)
            # dan seluruh rugi masuk Dana Cadangan
            nominal['jasa_modal'] = sum(modal.values())
            nominal['jasa_usaha'] = sum(usaha.values())
            nominal['cadangan'] = shu - sum(v for k, v in nominal.items() if k != 'cadangan')
            r._tulis(dict(
                {'nominal_' + k: v for k, v in nominal.items()},
                total_pendapatan=pendapatan, total_beban=beban, shu=shu,
                tanggal_hitung=fields.Datetime.now(), line_ids=[fields.Command.clear()] + baris))
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
                'SHU %(tahun)s baru dapat ditetapkan setelah semua periode tahun itu ditutup. '
                'Masih terbuka: %(periode)s.', tahun=self.tahun,
                periode=', '.join(terbuka.mapped('name'))))

    def action_tetapkan(self):
        """Tetapkan pembagian SHU dan buat jurnal penutup per 31 Desember."""
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
            tujuan = defaultdict(float)
            for k, _l, akun_kunci in POS_SHU:
                tujuan[akun_kunci] += r['nominal_' + k]
            for akun_kunci, nilai in tujuan.items():
                # rugi (cadangan negatif) didebit ke Dana Cadangan
                baris.append((Akun._sistem(akun_kunci), -nilai if nilai < 0 else 0, nilai if nilai > 0 else 0))
            jurnal = self.env['koperasi.jurnal']._buat_otomatis({
                'tanggal': r._rentang()[1],
                'keterangan': _('Jurnal penutup dan pembagian SHU tahun %s', r.tahun),
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
        """Catat pembayaran bagian anggota hari ini: Dr SHU Bagian Anggota / Cr Bank."""
        self._cek_operator()
        Akun = self.env['koperasi.akun']
        hari_ini = fields.Date.context_today(self)
        for r in self:
            if r.state != 'ditetapkan':
                raise UserError(_('Pembayaran hanya untuk SHU yang sudah ditetapkan.'))
            if not r.nominal_anggota:
                raise UserError(_('Tidak ada bagian anggota yang perlu dibayar.'))
            jurnal = self.env['koperasi.jurnal']._buat_otomatis({
                'tanggal': hari_ini,
                'keterangan': _('Pembayaran SHU %(tahun)s kepada %(n)d anggota', tahun=r.tahun, n=r.jumlah_anggota),
                'sumber': 'shu',
                'shu_id': r.id,
            }, [(Akun._sistem('shu_anggota'), r.nominal_anggota, 0), (Akun._sistem('bank'), 0, r.nominal_anggota)])
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
    _description = 'Bagian SHU per Anggota'
    _order = 'total desc, id'
    _rec_name = 'anggota_id'

    shu_id = fields.Many2one('koperasi.shu', string='Tutup Buku', required=True, index=True, ondelete='cascade')
    currency_id = fields.Many2one(related='shu_id.currency_id')
    anggota_id = fields.Many2one(
        'koperasi.anggota', string='Anggota', required=True, index=True, ondelete='restrict')
    mitra_id = fields.Many2one(related='anggota_id.mitra_id', store=True, index=True)
    dasar_simpanan = fields.Monetary('Simpanan Pokok + Wajib', help='Saldo pada akhir tahun buku.')
    dasar_jasa = fields.Monetary('Jasa + Biaya Admin Dibayar', help='Selama tahun buku.')
    jasa_modal = fields.Monetary('Jasa Modal')
    jasa_usaha = fields.Monetary('Jasa Usaha')
    total = fields.Monetary('Bagian SHU', compute='_compute_total', store=True)

    @api.depends('jasa_modal', 'jasa_usaha')
    def _compute_total(self):
        for r in self:
            r.total = r.jasa_modal + r.jasa_usaha


class KoperasiJurnal(models.Model):
    _inherit = 'koperasi.jurnal'

    shu_id = fields.Many2one(
        'koperasi.shu', string='Tutup Buku', readonly=True, index=True, ondelete='restrict')
