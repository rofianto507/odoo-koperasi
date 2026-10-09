import math

from dateutil.relativedelta import relativedelta
from markupsafe import Markup, escape

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

STATE_PINJAMAN = [
    ('draft', 'Draft'),
    ('diajukan', 'Menunggu Persetujuan'),
    ('disetujui', 'Menunggu Pencairan'),
    ('berjalan', 'Berjalan'),
    ('lunas', 'Lunas'),
    ('ditolak', 'Ditolak'),
]
# Pinjaman yang "menempati" jatah anggota: hanya boleh satu.
STATE_AKTIF = ('diajukan', 'disetujui', 'berjalan')
# State di mana admin mitra masih boleh mengubah pengajuan.
STATE_EDITABLE_MITRA = ('draft', 'ditolak')

FIELD_PENGAJUAN = {'anggota_id', 'produk_id', 'pokok', 'tenor'}
FIELD_ALUR = {
    'state', 'name', 'tanggal_ajukan', 'validator_mitra_id', 'penyetuju_id',
    'tanggal_setuju', 'tanggal_cair', 'tanggal_lunas',
}

ADMIN_MODE = [('persen', 'Persentase dari pokok'), ('nominal', 'Nominal rupiah')]


def rp(nilai):
    return 'Rp %s' % '{:,.0f}'.format(nilai or 0).replace(',', '.')


def persen(nilai):
    return ('%.2f' % (nilai or 0)).rstrip('0').rstrip('.').replace('.', ',') + '%'


def hitung_pinjaman(pokok, tenor, jasa_persen, admin_mode, admin_persen, admin_nominal):
    """Rincian pinjaman jasa flat: jasa tiap bulan dihitung dari pokok awal."""
    pokok, tenor = pokok or 0, tenor or 0
    jasa_bulan = round(pokok * (jasa_persen or 0) / 100)
    pokok_bulan = round(pokok / tenor) if tenor > 0 else 0
    if admin_mode == 'nominal':
        biaya_admin = admin_nominal or 0
    else:
        biaya_admin = round(pokok * (admin_persen or 0) / 100)
    total_jasa = jasa_bulan * max(tenor, 0)
    return {
        'jasa_bulan': jasa_bulan,
        'pokok_bulan': pokok_bulan,
        'angsuran_bulan': pokok_bulan + jasa_bulan,
        'total_jasa': total_jasa,
        'total_bayar': pokok + total_jasa,
        'biaya_admin': biaya_admin,
        'diterima': pokok - biaya_admin,
    }


def hitung_plafon(angsuran_maks, tenor, jasa_persen, kelipatan):
    """Pokok terbesar yang angsurannya masih <= angsuran_maks, dibulatkan ke
    bawah per kelipatan. angsuran = pokok x (1/tenor + jasa%)."""
    if angsuran_maks <= 0 or not tenor or tenor <= 0:
        return 0
    mentah = angsuran_maks / (1 / tenor + (jasa_persen or 0) / 100)
    kelipatan = kelipatan if kelipatan and kelipatan > 0 else 1
    return math.floor(mentah / kelipatan) * kelipatan


class KoperasiPinjamanProduk(models.Model):
    _name = 'koperasi.pinjaman.produk'
    _description = 'Produk Pinjaman Koperasi'
    _inherit = ['mail.thread', 'koperasi.mata.uang.mixin']
    _order = 'sequence, name'

    name = fields.Char('Nama Produk', required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True, tracking=True)
    keterangan = fields.Text('Keterangan')
    jasa_persen = fields.Float(
        'Jasa per Bulan (%)', digits=(6, 2), default=1.0, required=True, tracking=True,
        help='Biaya jasa untuk platform, flat dari pokok pinjaman, ditagihkan tiap bulan.')
    admin_mode = fields.Selection(
        ADMIN_MODE, string='Biaya Admin', default='persen', required=True, tracking=True,
        help='Dipotong satu kali dari dana yang dicairkan.')
    admin_persen = fields.Float('Biaya Admin (%)', digits=(6, 2), default=2.0, tracking=True)
    admin_nominal = fields.Monetary('Biaya Admin (Rp)', tracking=True)
    tenor_min = fields.Integer('Tenor Minimum (bulan)', default=3, required=True, tracking=True)
    tenor_max = fields.Integer('Tenor Maksimum (bulan)', default=12, required=True, tracking=True)
    pinjaman_count = fields.Integer('Jumlah Pinjaman', compute='_compute_pinjaman_count')

    # Simulasi: masukan tidak disimpan, hanya untuk menguji parameter di form.
    sim_pokok = fields.Monetary('Pokok', compute='_compute_sim_masukan', readonly=False)
    sim_tenor = fields.Integer('Tenor (bulan)', compute='_compute_sim_masukan', readonly=False)
    sim_gaji = fields.Monetary('Gaji Bersih', compute='_compute_sim_masukan', readonly=False)
    sim_simsula = fields.Monetary(
        'Simpanan Sukarela', compute='_compute_sim_masukan', readonly=False,
        help='Simpanan sukarela anggota per bulan, ikut mengurangi batas potongan.')
    sim_hasil = fields.Html('Hasil Simulasi', compute='_compute_sim_hasil', sanitize=False)

    @api.constrains('jasa_persen', 'admin_persen', 'admin_nominal', 'tenor_min', 'tenor_max')
    def _cek_parameter(self):
        for r in self:
            if r.jasa_persen < 0 or r.admin_persen < 0 or r.admin_nominal < 0:
                raise ValidationError(_('Jasa dan biaya admin tidak boleh negatif.'))
            if r.tenor_min < 1 or r.tenor_max < r.tenor_min:
                raise ValidationError(_(
                    'Tenor minimum paling sedikit 1 bulan dan tidak boleh melebihi tenor maksimum.'))

    def _compute_pinjaman_count(self):
        data = self.env['koperasi.pinjaman']._read_group(
            [('produk_id', 'in', self.ids)], ['produk_id'], ['__count'])
        jumlah = {produk.id: n for produk, n in data}
        for r in self:
            r.pinjaman_count = jumlah.get(r.id, 0)

    def _compute_sim_masukan(self):
        for r in self:
            r.sim_pokok = 12000000
            r.sim_tenor = min(max(12, r.tenor_min or 1), r.tenor_max or 12)
            r.sim_gaji = 8000000
            r.sim_simsula = 0

    @api.depends('sim_pokok', 'sim_tenor', 'sim_gaji', 'sim_simsula', 'jasa_persen',
                 'admin_mode', 'admin_persen', 'admin_nominal', 'tenor_min', 'tenor_max')
    def _compute_sim_hasil(self):
        cfg = self.env['koperasi.pengaturan']._get()
        for r in self:
            h = hitung_pinjaman(r.sim_pokok, r.sim_tenor, r.jasa_persen,
                                r.admin_mode, r.admin_persen, r.admin_nominal)
            batas = round((r.sim_gaji or 0) * cfg.batas_potongan / 100)
            potongan = cfg.simwa + (r.sim_simsula or 0)
            maks = max(batas - potongan, 0)
            plafon = hitung_plafon(maks, r.sim_tenor, r.jasa_persen, cfg.plafon_kelipatan)

            masalah = []
            if not r.tenor_min <= (r.sim_tenor or 0) <= r.tenor_max:
                masalah.append(_('Tenor %(tenor)s bulan di luar rentang produk (%(min)s-%(max)s bulan).',
                                 tenor=r.sim_tenor, min=r.tenor_min, max=r.tenor_max))
            if (r.sim_pokok or 0) > plafon:
                masalah.append(_('Pinjaman %(pokok)s melebihi plafon %(plafon)s.',
                                 pokok=rp(r.sim_pokok), plafon=rp(plafon)))
            if h['angsuran_bulan'] > maks:
                masalah.append(_('Angsuran %(angsuran)s melebihi angsuran maksimum %(maks)s.',
                                 angsuran=rp(h['angsuran_bulan']), maks=rp(maks)))
            if masalah:
                kelas, ikon = 'text-danger', 'fa-times-circle'
                putusan = ' '.join(masalah)
            else:
                kelas, ikon = 'text-success', 'fa-check-circle'
                putusan = _(
                    'Pinjaman %(pokok)s di bawah plafon. Angsuran %(angsuran)s di bawah %(maks)s.',
                    pokok=rp(r.sim_pokok), angsuran=rp(h['angsuran_bulan']), maks=rp(maks))

            if r.admin_mode == 'nominal':
                label_admin = _('Biaya admin (sekali di muka)')
            else:
                label_admin = _('Biaya admin (%s, sekali di muka)', persen(r.admin_persen))
            baris = [
                (_('Jasa per bulan (%(jasa)s x %(pokok)s)', jasa=persen(r.jasa_persen),
                   pokok=rp(r.sim_pokok)), rp(h['jasa_bulan'])),
                (_('Total jasa (%s bulan, ditagih bertahap)', r.sim_tenor or 0), rp(h['total_jasa'])),
                (_('Total pembayaran'), rp(h['total_bayar'])),
                (label_admin, rp(h['biaya_admin'])),
                (_('Diterima anggota'), rp(h['diterima'])),
                (_('Angsuran pertama'), _('Tagihan periode berikutnya')),
            ]
            tabel = Markup().join(
                Markup('<div class="d-flex justify-content-between border-bottom py-2">'
                       '<span>%s</span><b class="text-nowrap ms-3">%s</b></div>') % (k, v)
                for k, v in baris)
            rumus = _(
                'Rumus: %(maks)s / (1/%(tenor)s + %(jasa)s) = %(mentah)s, dibulatkan ke bawah '
                'kelipatan %(kelipatan)s.',
                maks=rp(maks), tenor=r.sim_tenor or 0, jasa=persen(r.jasa_persen),
                mentah=rp(hitung_plafon(maks, r.sim_tenor, r.jasa_persen, 1)),
                kelipatan=rp(cfg.plafon_kelipatan))
            r.sim_hasil = Markup(
                '<div class="rounded-3 p-3 mb-2 text-white" style="background:linear-gradient(135deg,#7c3aed,#4c1d95)">'
                '<div class="small fw-bold text-uppercase opacity-75">%(l_angsuran)s</div>'
                '<div class="fs-2 fw-bold">%(angsuran)s</div></div>'
                '%(tabel)s'
                '<div class="rounded-3 p-3 mt-3" style="background:rgba(124,58,237,.08)">'
                '<div class="fw-bold">%(l_plafon)s</div>'
                '<div class="small text-muted mb-2">%(l_plafon_ket)s</div>'
                '<div class="d-flex justify-content-between"><span>%(l_batas)s</span><span>%(batas)s</span></div>'
                '<div class="d-flex justify-content-between border-bottom pb-1"><span>%(l_potongan)s</span><span>- %(potongan)s</span></div>'
                '<div class="d-flex justify-content-between fw-bold pt-1"><span>%(l_maks)s</span><span>%(maks)s</span></div>'
                '<div class="d-flex justify-content-between fw-bold"><span>%(l_nilai_plafon)s</span><span>%(plafon)s</span></div>'
                '<div class="small text-muted mt-1">%(rumus)s</div>'
                '<div class="border-top mt-2 pt-2 %(kelas)s"><i class="fa %(ikon)s me-1"></i>%(putusan)s</div>'
                '</div>'
            ) % {
                'l_angsuran': _('Angsuran per bulan'),
                'angsuran': rp(h['angsuran_bulan']),
                'tabel': tabel,
                'l_plafon': _('Cek Plafon Anggota (gaji bersih %s)', rp(r.sim_gaji)),
                'l_plafon_ket': _('Batas pinjaman maksimum untuk anggota dengan gaji ini, '
                                  'terpisah dari pokok yang disimulasikan di atas.'),
                'l_batas': _('Batas potongan %s', persen(cfg.batas_potongan)),
                'batas': rp(batas),
                'l_potongan': _('Dikurangi wajib dan sukarela'),
                'potongan': rp(potongan),
                'l_maks': _('Angsuran maksimum'),
                'maks': rp(maks),
                'l_nilai_plafon': _('Plafon pinjaman'),
                'plafon': rp(plafon),
                'rumus': rumus,
                'kelas': kelas,
                'ikon': ikon,
                'putusan': escape(putusan),
            }

    def unlink(self):
        if self.env['koperasi.pinjaman'].sudo().search_count([('produk_id', 'in', self.ids)], limit=1):
            raise UserError(_(
                'Produk yang sudah dipakai pinjaman tidak dapat dihapus. Arsipkan saja.'))
        return super().unlink()


class KoperasiPinjaman(models.Model):
    _name = 'koperasi.pinjaman'
    _description = 'Pinjaman Anggota Koperasi'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'koperasi.mata.uang.mixin']
    _order = 'id desc'

    name = fields.Char('Nomor Pinjaman', readonly=True, copy=False, index=True)
    anggota_id = fields.Many2one(
        'koperasi.anggota', string='Anggota', required=True, index=True,
        ondelete='restrict', tracking=True, domain="[('state', '=', 'aktif')]")
    mitra_id = fields.Many2one(related='anggota_id.mitra_id', store=True, index=True)
    produk_id = fields.Many2one(
        'koperasi.pinjaman.produk', string='Produk Pinjaman', required=True,
        ondelete='restrict', tracking=True,
        default=lambda self: self.env['koperasi.pinjaman.produk'].search([], limit=1))
    pokok = fields.Monetary('Pokok Pinjaman', required=True, tracking=True)
    tenor = fields.Integer('Tenor (bulan)', required=True, default=12, tracking=True)
    keperluan = fields.Char('Keperluan')

    # Parameter produk disalin saat produk dipilih, supaya perubahan produk
    # kemudian hari tidak mengubah pinjaman yang sudah ada.
    jasa_persen = fields.Float(
        'Jasa per Bulan (%)', digits=(6, 2), compute='_compute_parameter', store=True)
    admin_mode = fields.Selection(ADMIN_MODE, compute='_compute_parameter', store=True)
    admin_persen = fields.Float(digits=(6, 2), compute='_compute_parameter', store=True)
    admin_nominal = fields.Monetary(compute='_compute_parameter', store=True)

    pokok_bulan = fields.Monetary('Angsuran Pokok / Bulan', compute='_compute_rincian', store=True)
    jasa_bulan = fields.Monetary('Jasa / Bulan', compute='_compute_rincian', store=True)
    angsuran_bulan = fields.Monetary('Angsuran / Bulan', compute='_compute_rincian', store=True)
    total_jasa = fields.Monetary('Total Jasa', compute='_compute_rincian', store=True)
    total_bayar = fields.Monetary('Total Pembayaran', compute='_compute_rincian', store=True)
    biaya_admin = fields.Monetary('Biaya Admin', compute='_compute_rincian', store=True)
    diterima = fields.Monetary('Diterima Anggota', compute='_compute_rincian', store=True)

    # Kelayakan: dihitung dari data anggota saat ini.
    gaji_bersih = fields.Monetary('Gaji Bersih', compute='_compute_kelayakan')
    batas_potongan = fields.Monetary('Batas Potongan Gaji', compute='_compute_kelayakan')
    potongan_simpanan = fields.Monetary('Potongan Simpanan', compute='_compute_kelayakan')
    angsuran_maks = fields.Monetary('Angsuran Maksimum', compute='_compute_kelayakan')
    plafon = fields.Monetary('Plafon Pinjaman', compute='_compute_kelayakan')
    layak = fields.Boolean('Layak', compute='_compute_kelayakan')
    pesan_kelayakan = fields.Text('Hasil Cek Kelayakan', compute='_compute_kelayakan')

    # Rekening tujuan pencairan: diambil dari data anggota (diubah di form anggota).
    rekening_bank = fields.Char('Bank Tujuan', compute='_compute_rekening')
    rekening_nomor = fields.Char('Nomor Rekening', compute='_compute_rekening')
    rekening_atas_nama = fields.Char('Atas Nama', compute='_compute_rekening')

    state = fields.Selection(
        STATE_PINJAMAN, string='Status', default='draft', required=True,
        tracking=True, copy=False, index=True)
    alasan_tolak = fields.Text('Alasan Penolakan', copy=False)
    pengaju_id = fields.Many2one(
        'res.users', string='Diinput Oleh', readonly=True, copy=False,
        default=lambda self: self.env.user)
    tanggal_ajukan = fields.Datetime('Tanggal Diajukan', readonly=True, copy=False)
    validator_mitra_id = fields.Many2one(
        'res.users', string='Divalidasi Mitra Oleh', readonly=True, copy=False)
    penyetuju_id = fields.Many2one('res.users', string='Disetujui Oleh', readonly=True, copy=False)
    tanggal_setuju = fields.Datetime('Tanggal Disetujui', readonly=True, copy=False)
    tanggal_cair = fields.Date('Tanggal Pencairan', readonly=True, copy=False)
    tanggal_lunas = fields.Date('Tanggal Lunas', readonly=True, copy=False)

    angsuran_ids = fields.One2many(
        'koperasi.pinjaman.angsuran', 'pinjaman_id', string='Jadwal Angsuran', readonly=True)
    angsuran_dibayar = fields.Integer('Angsuran Dibayar', compute='_compute_sisa', store=True)
    sisa_pokok = fields.Monetary('Sisa Pokok', compute='_compute_sisa', store=True)

    _pokok_positif = models.Constraint('CHECK(pokok > 0)', 'Pokok pinjaman harus lebih dari nol.')

    # ------------------------------------------------------------------
    # Perhitungan
    # ------------------------------------------------------------------
    @api.depends('produk_id')
    def _compute_parameter(self):
        for r in self:
            p = r.produk_id
            r.jasa_persen = p.jasa_persen
            r.admin_mode = p.admin_mode
            r.admin_persen = p.admin_persen
            r.admin_nominal = p.admin_nominal

    @api.depends('pokok', 'tenor', 'jasa_persen', 'admin_mode', 'admin_persen', 'admin_nominal')
    def _compute_rincian(self):
        for r in self:
            h = hitung_pinjaman(r.pokok, r.tenor, r.jasa_persen,
                                r.admin_mode, r.admin_persen, r.admin_nominal)
            r.pokok_bulan = h['pokok_bulan']
            r.jasa_bulan = h['jasa_bulan']
            r.angsuran_bulan = h['angsuran_bulan']
            r.total_jasa = h['total_jasa']
            r.total_bayar = h['total_bayar']
            r.biaya_admin = h['biaya_admin']
            r.diterima = h['diterima']

    @api.depends('anggota_id', 'produk_id', 'pokok', 'tenor', 'jasa_persen', 'state')
    def _compute_kelayakan(self):
        cfg = self.env['koperasi.pengaturan']._get()
        for r in self:
            a = r.anggota_id
            r.gaji_bersih = a.gaji_bersih
            r.batas_potongan = round((a.gaji_bersih or 0) * cfg.batas_potongan / 100)
            r.potongan_simpanan = a._potongan_simpanan() if a else 0
            r.angsuran_maks = max(r.batas_potongan - r.potongan_simpanan, 0)
            r.plafon = hitung_plafon(r.angsuran_maks, r.tenor, r.jasa_persen, cfg.plafon_kelipatan)
            masalah = r._masalah_kelayakan()
            r.layak = not masalah
            r.pesan_kelayakan = '\n'.join(masalah) if masalah else _(
                'Pinjaman %(pokok)s di bawah plafon %(plafon)s. Angsuran %(angsuran)s '
                'di bawah angsuran maksimum %(maks)s.',
                pokok=rp(r.pokok), plafon=rp(r.plafon),
                angsuran=rp(r.angsuran_bulan), maks=rp(r.angsuran_maks))

    @api.depends('anggota_id.bank_tujuan', 'anggota_id.bank_lainnya',
                 'anggota_id.nomor_rekening', 'anggota_id.atas_nama')
    def _compute_rekening(self):
        for r in self:
            a = r.anggota_id
            r.rekening_bank = a._nama_bank() if a else False
            r.rekening_nomor = a.nomor_rekening
            r.rekening_atas_nama = a.atas_nama

    def _teks_rekening(self):
        self.ensure_one()
        if not self.rekening_nomor:
            return _('rekening belum diisi')
        return '%s %s a.n. %s' % (self.rekening_bank, self.rekening_nomor, self.rekening_atas_nama)

    def _masalah_kelayakan(self):
        """Daftar alasan pinjaman ini belum layak (kosong = layak)."""
        self.ensure_one()
        a, p = self.anggota_id, self.produk_id
        if not a or not p:
            return [_('Pilih anggota dan produk pinjaman.')]
        masalah = []
        if a.state != 'aktif':
            masalah.append(_('%s belum menjadi anggota aktif.', a.name))
        if not a.gaji_bersih:
            masalah.append(_('Gaji bersih %s belum diisi di data anggota.', a.name))
        if not p.tenor_min <= self.tenor <= p.tenor_max:
            masalah.append(_(
                'Tenor %(tenor)s bulan di luar rentang produk (%(min)s-%(max)s bulan).',
                tenor=self.tenor, min=p.tenor_min, max=p.tenor_max))
        lain = self.sudo().search([
            ('anggota_id', '=', a.id), ('state', 'in', STATE_AKTIF),
            ('id', '!=', self._origin.id or 0)], limit=1)
        if lain:
            masalah.append(_(
                '%(anggota)s masih punya pinjaman %(nomor)s (%(status)s). Hanya satu '
                'pinjaman aktif per anggota.',
                anggota=a.name, nomor=lain.name, status=dict(STATE_PINJAMAN)[lain.state]))
        if a.gaji_bersih:
            if self.angsuran_bulan > self.angsuran_maks:
                masalah.append(_(
                    'Angsuran %(angsuran)s melebihi angsuran maksimum %(maks)s.',
                    angsuran=rp(self.angsuran_bulan), maks=rp(self.angsuran_maks)))
            if self.pokok > self.plafon:
                masalah.append(_(
                    'Pinjaman %(pokok)s melebihi plafon %(plafon)s.',
                    pokok=rp(self.pokok), plafon=rp(self.plafon)))
        return masalah

    def _cek_kelayakan(self):
        for r in self:
            masalah = r._masalah_kelayakan()
            if masalah:
                raise UserError('\n'.join(masalah))

    @api.depends('angsuran_ids.status', 'angsuran_ids.pokok', 'pokok')
    def _compute_sisa(self):
        for r in self:
            dibayar = r.angsuran_ids.filtered(lambda x: x.status == 'dibayar')
            r.angsuran_dibayar = len(dibayar)
            r.sisa_pokok = r.pokok - sum(dibayar.mapped('pokok')) if r.angsuran_ids else r.pokok

    # ------------------------------------------------------------------
    # create / write / unlink guard
    # ------------------------------------------------------------------
    def _is_operator(self):
        return self.env.user.has_group('koperasi.group_koperasi_operator')

    def _cek_operator(self):
        if not self._is_operator():
            raise UserError(_('Hanya operator pusat yang dapat melakukan aksi ini.'))

    @api.model_create_multi
    def create(self, vals_list):
        terbatas = not self._is_operator() and not self.env.su
        for vals in vals_list:
            if terbatas:
                # Admin mitra tidak boleh melompati persetujuan operator.
                vals['state'] = 'draft'
                for f in FIELD_ALUR - {'state'}:
                    vals.pop(f, None)
            if not vals.get('name'):
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code('koperasi.pinjaman')
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if not self._is_operator():
                if any(r.state not in STATE_EDITABLE_MITRA for r in self):
                    raise UserError(_(
                        'Pinjaman yang sudah diajukan tidak dapat diubah oleh admin mitra. '
                        'Hubungi operator pusat.'))
                if (FIELD_ALUR | {'alasan_tolak'}) & vals.keys():
                    raise UserError(_('Anda tidak berhak mengubah field tersebut.'))
            if FIELD_PENGAJUAN & vals.keys() \
                    and any(r.state in ('disetujui', 'berjalan', 'lunas') for r in self):
                raise UserError(_(
                    'Pinjaman yang sudah disetujui tidak dapat diubah nominal, tenor, '
                    'produk, atau anggotanya.'))
        return super().write(vals)

    def unlink(self):
        if any(r.state != 'draft' for r in self):
            raise UserError(_('Hanya pinjaman berstatus Draft yang dapat dihapus.'))
        return super().unlink()

    # ------------------------------------------------------------------
    # Alur
    # ------------------------------------------------------------------
    def action_ajukan(self):
        """Admin mitra memvalidasi pengajuan anggotanya dan meneruskannya ke operator."""
        for r in self:
            if r.state not in STATE_EDITABLE_MITRA:
                raise UserError(_('Hanya pinjaman Draft/Ditolak yang dapat diajukan.'))
            r._cek_kelayakan()
            # sudo: admin mitra tidak boleh menulis state langsung (lihat write()),
            # tapi validasi mitra sah dilakukan olehnya. Record rule sudah
            # membatasi self ke pinjaman mitranya.
            r.sudo().write({
                'state': 'diajukan',
                'tanggal_ajukan': fields.Datetime.now(),
                'validator_mitra_id': self.env.user.id,
                'alasan_tolak': False,
            })

    def action_setujui(self):
        self._cek_operator()
        for r in self:
            if r.state != 'diajukan':
                raise UserError(_('Hanya pinjaman berstatus Menunggu Persetujuan yang dapat disetujui.'))
            r._cek_kelayakan()
            r.write({
                'state': 'disetujui',
                'penyetuju_id': self.env.user.id,
                'tanggal_setuju': fields.Datetime.now(),
            })

    def action_cairkan(self):
        """Catat pencairan dan buat jadwal angsuran mulai periode berikutnya."""
        self._cek_operator()
        Periode = self.env['koperasi.periode']
        hari_ini = fields.Date.context_today(self)
        for r in self:
            if r.state != 'disetujui':
                raise UserError(_('Hanya pinjaman berstatus Menunggu Pencairan yang dapat dicairkan.'))
            awal = hari_ini.replace(day=1)
            sisa = r.pokok
            jadwal = []
            for ke in range(1, r.tenor + 1):
                # pembulatan pokok/tenor dihabiskan di angsuran terakhir
                pokok = sisa if ke == r.tenor else min(r.pokok_bulan, sisa)
                sisa -= pokok
                jadwal.append({
                    'pinjaman_id': r.id,
                    'ke': ke,
                    'periode_id': Periode._untuk_tanggal(awal + relativedelta(months=ke)).id,
                    'pokok': pokok,
                    'jasa': r.jasa_bulan,
                })
            self.env['koperasi.pinjaman.angsuran'].sudo().create(jadwal)
            r.write({'state': 'berjalan', 'tanggal_cair': hari_ini})
            r.message_post(body=_(
                'Pinjaman dicairkan: pokok %(pokok)s, biaya admin %(admin)s, diterima anggota '
                '%(diterima)s ke %(rekening)s. Angsuran %(angsuran)s x %(tenor)s bulan mulai %(mulai)s.',
                pokok=rp(r.pokok), admin=rp(r.biaya_admin), diterima=rp(r.diterima),
                rekening=r._teks_rekening(),
                angsuran=rp(r.angsuran_bulan), tenor=r.tenor,
                mulai=r.angsuran_ids.sorted('ke')[:1].periode_id.name))

    def action_tolak(self):
        self._cek_operator()
        for r in self:
            if r.state not in ('diajukan', 'disetujui'):
                raise UserError(_('Hanya pinjaman yang belum dicairkan yang dapat ditolak.'))
            if not r.alasan_tolak:
                raise UserError(_('Alasan penolakan wajib diisi.'))
            r.write({'state': 'ditolak', 'penyetuju_id': False, 'tanggal_setuju': False})

    def action_buka_tolak_wizard(self):
        self.ensure_one()
        self._cek_operator()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Alasan Penolakan'),
            'res_model': 'koperasi.tolak.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_res_model': self._name, 'default_res_id': self.id},
        }

    def _perbarui_lunas(self):
        """Lunas otomatis bila semua angsuran dibayar; kembali Berjalan bila
        validasi pembayaran di tagihan dibatalkan."""
        hari_ini = fields.Date.context_today(self)
        for r in self.sudo():
            semua = r.angsuran_ids and all(x.status == 'dibayar' for x in r.angsuran_ids)
            if r.state == 'berjalan' and semua:
                r.write({'state': 'lunas', 'tanggal_lunas': hari_ini})
            elif r.state == 'lunas' and not semua:
                r.write({'state': 'berjalan', 'tanggal_lunas': False})


class KoperasiPinjamanAngsuran(models.Model):
    _name = 'koperasi.pinjaman.angsuran'
    _description = 'Jadwal Angsuran Pinjaman'
    _order = 'pinjaman_id desc, ke'
    _rec_name = 'ke'

    pinjaman_id = fields.Many2one(
        'koperasi.pinjaman', string='Pinjaman', required=True, index=True,
        ondelete='cascade')
    anggota_id = fields.Many2one(related='pinjaman_id.anggota_id', store=True, index=True)
    mitra_id = fields.Many2one(related='pinjaman_id.mitra_id', store=True, index=True)
    currency_id = fields.Many2one(related='pinjaman_id.currency_id')
    ke = fields.Integer('Angsuran Ke', required=True)
    periode_id = fields.Many2one(
        'koperasi.periode', string='Periode', required=True, ondelete='restrict')
    jatuh_tempo = fields.Date(related='periode_id.tanggal_mulai', store=True, index=True)
    pokok = fields.Monetary('Pokok')
    jasa = fields.Monetary('Jasa')
    total = fields.Monetary('Jumlah', compute='_compute_total', store=True)
    tagihan_line_id = fields.Many2one(
        'koperasi.tagihan.line', string='Baris Tagihan', readonly=True, copy=False,
        index=True, ondelete='set null')
    tagihan_id = fields.Many2one(related='tagihan_line_id.tagihan_id', string='Tagihan')
    status = fields.Selection(
        [('terjadwal', 'Terjadwal'), ('ditagihkan', 'Ditagihkan'), ('dibayar', 'Dibayar')],
        string='Status', compute='_compute_status', store=True)

    @api.depends('pokok', 'jasa')
    def _compute_total(self):
        for r in self:
            r.total = r.pokok + r.jasa

    @api.depends('tagihan_line_id', 'tagihan_line_id.status')
    def _compute_status(self):
        for r in self:
            line = r.tagihan_line_id
            if not line:
                r.status = 'terjadwal'
            else:
                r.status = 'dibayar' if line.status == 'dibayar' else 'ditagihkan'
