import re

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

from .koperasi_pinjaman import STATE_AKTIF, hitung_plafon, persen, rp

STATE_SELECTION = [
    ('draft', 'Draft'),
    ('menunggu', 'Menunggu Validasi'),
    ('aktif', 'Aktif'),
    ('ditolak', 'Ditolak'),
    ('keluar_diajukan', 'Menunggu Persetujuan Keluar'),
    ('keluar', 'Keluar'),
]

# Bank yang umum di Indonesia untuk rekening pencairan. 'lainnya' membuka isian nama bank.
BANK_SELECTION = [
    ('bca', 'BCA'),
    ('bri', 'BRI'),
    ('mandiri', 'Bank Mandiri'),
    ('bni', 'BNI'),
    ('btn', 'BTN'),
    ('bsi', 'BSI (Bank Syariah Indonesia)'),
    ('cimb', 'CIMB Niaga'),
    ('permata', 'Bank Permata'),
    ('danamon', 'Bank Danamon'),
    ('mega', 'Bank Mega'),
    ('lainnya', 'Bank Lainnya'),
]

# State di mana admin mitra masih boleh mengubah data anggota.
STATE_EDITABLE_MITRA = ('draft', 'ditolak')

# Field yang tetap boleh diubah admin mitra setelah anggota diajukan/aktif (tidak ada lagi).
FIELD_MITRA_SELALU = frozenset()


class KoperasiAnggota(models.Model):
    _name = 'koperasi.anggota'
    _description = 'Anggota Koperasi (Pegawai Mitra)'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'koperasi.kontak.mixin',
                'koperasi.mata.uang.mixin']
    _order = 'id desc'

    name = fields.Char('Nama Lengkap', required=True, index=True, tracking=True)
    nomor_anggota = fields.Char(
        'Nomor Anggota', readonly=True, copy=False, index=True,
        help='Diberikan otomatis saat anggota divalidasi operator.')
    active = fields.Boolean(default=True)

    # Data pribadi (alamat, email, kontak: lihat koperasi.kontak.mixin)
    nik = fields.Char('NIK (KTP)')
    tempat_lahir = fields.Char('Tempat Lahir')
    tanggal_lahir = fields.Date('Tanggal Lahir')
    jenis_kelamin = fields.Selection(
        [('l', 'Laki-laki'), ('p', 'Perempuan')], string='Jenis Kelamin')
    telepon = fields.Char('Telp / WA')

    # Data kepegawaian
    mitra_id = fields.Many2one(
        'koperasi.mitra', string='Mitra', required=True, index=True,
        ondelete='restrict', tracking=True,
        default=lambda self: self.env.user.koperasi_mitra_id)
    nip = fields.Char('NIP / ID Karyawan', required=True, tracking=True)
    jabatan = fields.Char('Jabatan')
    departemen = fields.Char('Departemen / Unit')
    status_pegawai = fields.Selection(
        [('kontrak', 'Kontrak'), ('tetap', 'Tetap')], string='Status Pegawai',
        tracking=True)
    tanggal_masuk_kerja = fields.Date('Mulai Bekerja')
    tanggal_bergabung = fields.Date(
        'Tanggal Bergabung', readonly=True, copy=False,
        help='Terisi otomatis saat anggota divalidasi operator.')
    tanggal_keluar = fields.Date(
        'Tanggal Keluar', readonly=True, copy=False,
        help='Terisi saat pengajuan keluar disetujui operator.')

    # Data gaji: nilai yang berlaku sekarang; tiap perubahan dicatat ke gaji_ids.
    gaji_bersih = fields.Monetary('Gaji Bersih', tracking=True)
    gaji_berlaku_mulai = fields.Date('Berlaku Mulai')
    gaji_ids = fields.One2many(
        'koperasi.anggota.gaji', 'anggota_id', string='Riwayat Gaji', readonly=True)

    # Rekening pencairan pinjaman
    bank_tujuan = fields.Selection(BANK_SELECTION, string='Bank Tujuan', tracking=True)
    bank_lainnya = fields.Char('Nama Bank', tracking=True)
    nomor_rekening = fields.Char('Nomor Rekening', tracking=True)
    atas_nama = fields.Char('Atas Nama', tracking=True)

    tagihan_line_ids = fields.One2many(
        'koperasi.tagihan.line', 'anggota_id', string='Riwayat Tagihan', readonly=True)
    tunggakan_total = fields.Monetary(
        'Tunggakan', compute='_compute_tunggakan',
        help='Angsuran yang belum dibayar dan akan ditagihkan kembali.')

    pinjaman_ids = fields.One2many(
        'koperasi.pinjaman', 'anggota_id', string='Pinjaman', readonly=True)
    pinjaman_sisa = fields.Monetary('Sisa Pinjaman', compute='_compute_pinjaman')
    angsuran_sisa = fields.Integer('Sisa Angsuran', compute='_compute_pinjaman')
    plafon_info = fields.Html('Simulasi Plafon', compute='_compute_plafon_info', sanitize=False)

    # Validasi
    state = fields.Selection(
        STATE_SELECTION, string='Status', default='draft', required=True,
        tracking=True, copy=False)
    pendaftar_id = fields.Many2one(
        'res.users', string='Didaftarkan Oleh', readonly=True, copy=False,
        default=lambda self: self.env.user)
    tanggal_ajukan = fields.Datetime('Tanggal Diajukan', readonly=True, copy=False)
    validator_id = fields.Many2one('res.users', string='Divalidasi Oleh', readonly=True, copy=False)
    tanggal_validasi = fields.Datetime('Tanggal Validasi', readonly=True, copy=False)
    alasan_tolak = fields.Text('Alasan Penolakan', copy=False)

    _nip_mitra_uniq = models.Constraint(
        'UNIQUE(mitra_id, nip)', 'NIP sudah terdaftar pada mitra ini.')
    _nomor_anggota_uniq = models.Constraint(
        'UNIQUE(nomor_anggota)', 'Nomor anggota harus unik.')

    # ------------------------------------------------------------------
    # Kontak (res.partner)
    # ------------------------------------------------------------------
    _partner_sync_extra = ('mitra_id',)

    def _partner_field_map(self):
        return dict(super()._partner_field_map(), jabatan='function')

    def _partner_vals(self, fnames=None):
        vals = super()._partner_vals(fnames)
        if fnames is None or 'mitra_id' in fnames:
            vals['parent_id'] = self.mitra_id.partner_id.id
        return vals

    def _partner_vals_baru(self):
        # Bukan 'contact': kontak bertipe contact saling menimpa alamat dengan
        # perusahaan induknya, padahal alamat anggota adalah alamat rumahnya.
        return {'type': 'other'}

    # ------------------------------------------------------------------
    # Gaji
    # ------------------------------------------------------------------
    def _catat_riwayat_gaji(self):
        """Tambah baris riwayat bila gaji bersih berubah; bila hanya tanggalnya
        yang dikoreksi, perbarui baris terakhir.

        sudo: riwayat tidak bisa ditulis langsung oleh pengguna, hanya lewat
        perubahan gaji di anggota yang sudah lolos pengecekan write().
        """
        Gaji = self.env['koperasi.anggota.gaji'].sudo()
        for r in self.sudo():
            terakhir = Gaji.search([('anggota_id', '=', r.id)], order='id desc', limit=1)
            if not terakhir and not r.gaji_bersih:
                continue
            tanggal = r.gaji_berlaku_mulai or fields.Date.context_today(r)
            if not terakhir or r.currency_id.compare_amounts(terakhir.gaji_bersih, r.gaji_bersih):
                Gaji.create({
                    'anggota_id': r.id,
                    'tanggal_berlaku': tanggal,
                    'gaji_bersih': r.gaji_bersih,
                    'perubahan': r.gaji_bersih - terakhir.gaji_bersih if terakhir else 0,
                })
            elif terakhir.tanggal_berlaku != tanggal:
                terakhir.tanggal_berlaku = tanggal

    # ------------------------------------------------------------------
    # Tunggakan
    # ------------------------------------------------------------------
    @api.depends('tagihan_line_ids.status', 'tagihan_line_ids.dibawa_ke_id',
                 'tagihan_line_ids.total', 'tagihan_line_ids.tunggakan')
    def _compute_tunggakan(self):
        """Tunggakan = baris Belum Dibayar yang belum ditagih ulang (nilai utuh)
        + bagian tunggakan pada baris yang sudah memuatnya tetapi belum divalidasi."""
        Line = self.env['koperasi.tagihan.line']
        tunggakan = {}
        for anggota, jumlah in Line._read_group(
                [('anggota_id', 'in', self.ids), ('status', '=', 'belum'),
                 ('dibawa_ke_id', '=', False)], ['anggota_id'], ['total:sum']):
            tunggakan[anggota.id] = tunggakan.get(anggota.id, 0) + (jumlah or 0)
        for anggota, jumlah in Line._read_group(
                [('anggota_id', 'in', self.ids), ('status', '=', 'menunggu')],
                ['anggota_id'], ['tunggakan:sum']):
            tunggakan[anggota.id] = tunggakan.get(anggota.id, 0) + (jumlah or 0)
        for r in self:
            r.tunggakan_total = tunggakan.get(r.id, 0)

    @api.depends('pinjaman_ids.state', 'pinjaman_ids.sisa_pokok', 'pinjaman_ids.angsuran_dibayar')
    def _compute_pinjaman(self):
        for r in self:
            berjalan = r.pinjaman_ids.filtered(lambda p: p.state == 'berjalan')
            r.pinjaman_sisa = sum(berjalan.mapped('sisa_pokok'))
            r.angsuran_sisa = sum(p.tenor - p.angsuran_dibayar for p in berjalan)

    @api.depends('gaji_bersih', 'pinjaman_ids.state')
    def _compute_plafon_info(self):
        """Ringkasan batas potongan gaji anggota: angsuran maksimum dan plafon
        pinjaman tiap produk pada tenor terpanjangnya."""
        cfg = self.env['koperasi.pengaturan']._get()
        produk = self.env['koperasi.pinjaman.produk'].sudo().search([])
        baris_html = Markup(
            '<div class="d-flex justify-content-between%s"><span>%s</span>'
            '<span class="text-nowrap ms-3">%s</span></div>')
        for r in self:
            if not r.gaji_bersih:
                r.plafon_info = Markup('<div class="text-muted small">%s</div>') % _(
                    'Isi gaji bersih untuk melihat simulasi plafon angsuran.')
                continue
            maks = round(r.gaji_bersih * cfg.batas_potongan / 100)
            berjalan = r.pinjaman_ids.filtered(lambda p: p.state == 'berjalan')
            baris = [
                baris_html % (' fw-bold', _('Angsuran maksimum (%s dari gaji)', persen(cfg.batas_potongan)), rp(maks)),
            ]
            if berjalan:
                angsuran = sum(berjalan.mapped('angsuran_bulan'))
                baris.append(baris_html % (
                    ' text-muted', _('Angsuran pinjaman berjalan'), rp(angsuran)))
            else:
                baris += [
                    baris_html % ('', _('Plafon %(produk)s (%(tenor)s bulan)',
                                        produk=p.name, tenor=p.tenor_max),
                                  rp(hitung_plafon(maks, p.tenor_max, p.jasa_persen,
                                                   cfg.plafon_kelipatan)))
                    for p in produk
                ]
            r.plafon_info = Markup(
                '<div class="rounded-3 p-3 mt-2" style="background:rgba(124,58,237,.08)">'
                '<div class="fw-bold mb-1">%s</div>%s</div>'
            ) % (_('Simulasi Plafon Angsuran'), Markup().join(baris))

    def action_riwayat_anggota(self):
        """Riwayat tagihan dan pinjaman anggota (PDF) sejak tagihan pertamanya sampai bulan ini."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_url',
            'url': '/koperasi/laporan/pdf?jenis=anggota&anggota=%d' % self.id,
            'target': 'new',
        }

    def action_lihat_tagihan(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Tagihan %s', self.name),
            'res_model': 'koperasi.tagihan.line',
            'view_mode': 'list',
            'domain': [('anggota_id', '=', self.id)],
        }

    def action_lihat_pinjaman(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Pinjaman %s', self.name),
            'res_model': 'koperasi.pinjaman',
            'view_mode': 'list,form',
            'domain': [('anggota_id', '=', self.id)],
            'context': {'default_anggota_id': self.id},
        }

    @api.constrains('nomor_rekening')
    def _cek_nomor_rekening(self):
        for r in self:
            if r.nomor_rekening and not re.fullmatch(r'[0-9][0-9 \-]{3,29}', r.nomor_rekening.strip()):
                raise ValidationError(_(
                    'Nomor rekening hanya boleh berisi angka (spasi dan tanda hubung diperbolehkan).'))

    @api.onchange('bank_tujuan')
    def _onchange_bank_tujuan(self):
        if self.bank_tujuan != 'lainnya':
            self.bank_lainnya = False

    def _nama_bank(self):
        """Nama bank untuk ditampilkan/dicetak, termasuk bank yang diketik manual."""
        self.ensure_one()
        if self.bank_tujuan == 'lainnya':
            return self.bank_lainnya or ''
        return dict(BANK_SELECTION).get(self.bank_tujuan, '')

    def _is_operator(self):
        return self.env.user.has_group('koperasi.group_koperasi_operator')

    def _cek_operator(self):
        if not self._is_operator():
            raise UserError(_('Hanya operator pusat yang dapat melakukan validasi.'))

    # ------------------------------------------------------------------
    # create / write / unlink guard
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        if not self._is_operator() and not self.env.su:
            mitra = self.env.user.koperasi_mitra_id
            if not mitra:
                raise UserError(_('Akun Anda belum terhubung ke mitra manapun.'))
            for vals in vals_list:
                # Admin mitra hanya boleh mendaftarkan untuk mitranya sendiri
                # dan tidak boleh melompati validasi dengan set state/nomor.
                vals['mitra_id'] = mitra.id
                vals['state'] = 'draft'
                for f in ('nomor_anggota', 'validator_id', 'tanggal_validasi',
                          'tanggal_bergabung', 'partner_id'):
                    vals.pop(f, None)
        anggota = super().create(vals_list)
        anggota._catat_riwayat_gaji()
        return anggota

    def write(self, vals):
        if not self._is_operator() and not self.env.su:
            if not set(vals) <= FIELD_MITRA_SELALU \
                    and any(r.state not in STATE_EDITABLE_MITRA for r in self):
                raise UserError(_(
                    'Data anggota yang sudah diajukan atau aktif tidak dapat '
                    'diubah oleh admin mitra. Hubungi operator pusat.'))
            # Field alur validasi hanya boleh diubah lewat tombol action_*.
            terlarang = {'state', 'nomor_anggota', 'validator_id', 'tanggal_validasi',
                         'tanggal_bergabung', 'mitra_id', 'partner_id', 'tanggal_ajukan'}
            if terlarang & set(vals):
                raise UserError(_('Anda tidak berhak mengubah field tersebut.'))
        res = super().write(vals)
        if {'gaji_bersih', 'gaji_berlaku_mulai'} & vals.keys():
            self._catat_riwayat_gaji()
        return res

    def unlink(self):
        if not self._is_operator() and not self.env.su \
                and any(r.state != 'draft' for r in self):
            raise UserError(_('Hanya anggota berstatus Draft yang dapat dihapus.'))
        return super().unlink()

    # ------------------------------------------------------------------
    # Alur validasi
    # ------------------------------------------------------------------
    def action_ajukan(self):
        for r in self:
            if r.state not in STATE_EDITABLE_MITRA:
                raise UserError(_('Hanya anggota Draft/Ditolak yang dapat diajukan.'))
            # sudo: admin mitra tidak boleh menulis state langsung (lihat write()),
            # tapi submit itu sendiri sah dilakukan olehnya. Record rule sudah
            # membatasi self hanya ke anggota mitranya sebelum sampai sini.
            r.sudo().write({
                'state': 'menunggu',
                'tanggal_ajukan': fields.Datetime.now(),
                'alasan_tolak': False,
            })

    def action_setujui(self):
        self._cek_operator()
        for r in self:
            if r.state != 'menunggu':
                raise UserError(_('Hanya anggota berstatus Menunggu Validasi yang dapat disetujui.'))
            if not r.mitra_id.active:
                raise UserError(_('Mitra %s tidak aktif.', r.mitra_id.name))
            r.write({
                'state': 'aktif',
                'nomor_anggota': r.nomor_anggota or self.env['ir.sequence'].next_by_code('koperasi.anggota'),
                'validator_id': self.env.user.id,
                'tanggal_validasi': fields.Datetime.now(),
                'tanggal_bergabung': fields.Date.context_today(r),
                'alasan_tolak': False,
            })

    def action_tolak(self):
        self._cek_operator()
        for r in self:
            if r.state not in ('menunggu', 'keluar_diajukan'):
                raise UserError(_(
                    'Hanya anggota berstatus Menunggu Validasi atau Menunggu Persetujuan Keluar '
                    'yang dapat ditolak.'))
            if not r.alasan_tolak:
                raise UserError(_('Alasan penolakan wajib diisi.'))
            r.write({
                # pengajuan keluar yang ditolak kembali Aktif, bukan Ditolak
                'state': 'ditolak' if r.state == 'menunggu' else 'aktif',
                'validator_id': self.env.user.id,
                'tanggal_validasi': fields.Datetime.now(),
            })

    # ------------------------------------------------------------------
    # Keluar dari keanggotaan: tanpa simpanan, hanya alur persetujuan status
    # ------------------------------------------------------------------
    def _cek_boleh_keluar(self):
        self.ensure_one()
        pinjaman = self.pinjaman_ids.filtered(lambda p: p.state in STATE_AKTIF)
        if pinjaman:
            raise UserError(_(
                '%(anggota)s masih punya pinjaman %(nomor)s yang belum lunas atau masih diproses. '
                'Selesaikan dulu sebelum anggota keluar.', anggota=self.name, nomor=pinjaman[0].name))
        if self.tunggakan_total:
            raise UserError(_(
                '%(anggota)s masih punya tunggakan %(jumlah)s. Lunasi dulu sebelum anggota keluar.',
                anggota=self.name, jumlah=rp(self.tunggakan_total)))
        menggantung = self.env['koperasi.tagihan.line'].sudo().search([
            ('anggota_id', '=', self.id), ('status', '=', 'menunggu'),
            ('tagihan_id.state', '!=', 'draft')], limit=1)
        if menggantung:
            raise UserError(_(
                'Tagihan %(tagihan)s untuk %(anggota)s belum divalidasi. Validasi dulu pembayarannya '
                'sebelum anggota keluar.', tagihan=menggantung.tagihan_id.name, anggota=self.name))

    def action_ajukan_keluar(self):
        for r in self:
            if r.state != 'aktif':
                raise UserError(_('Hanya anggota Aktif yang dapat diajukan keluar.'))
            r._cek_boleh_keluar()
            # sudo: admin mitra tidak boleh menulis state langsung (lihat write()),
            # tapi pengajuan ini sendiri sah dilakukan olehnya untuk anggota mitranya.
            r.sudo().write({
                'state': 'keluar_diajukan',
                'tanggal_ajukan': fields.Datetime.now(),
                'alasan_tolak': False,
            })

    def action_setujui_keluar(self):
        self._cek_operator()
        for r in self:
            if r.state != 'keluar_diajukan':
                raise UserError(_(
                    'Hanya pengajuan keluar yang Menunggu Persetujuan yang dapat disetujui.'))
            r._cek_boleh_keluar()
            # baris anggota di tagihan yang masih Draft tidak lagi ditagihkan
            self.env['koperasi.tagihan.line'].sudo().search([
                ('anggota_id', '=', r.id), ('tagihan_id.state', '=', 'draft')]).unlink()
            r.write({
                'state': 'keluar',
                'tanggal_keluar': fields.Date.context_today(r),
                'validator_id': self.env.user.id,
                'tanggal_validasi': fields.Datetime.now(),
            })
            r.message_post(body=_('Keluar dari keanggotaan, disetujui oleh %s.', self.env.user.name))

    # ------------------------------------------------------------------
    # Aksi massal dari daftar (menu Tindakan): yang tidak memenuhi syarat dilewati, bukan digagalkan
    # ------------------------------------------------------------------
    def _notifikasi_massal(self, pesan, jenis='success'):
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'type': jenis, 'message': pesan,
                       'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}},
        }

    def action_ajukan_massal(self):
        siap = self.filtered(lambda r: r.state in STATE_EDITABLE_MITRA)
        siap.action_ajukan()
        lewat = len(self) - len(siap)
        pesan = _('%d anggota diajukan untuk divalidasi.', len(siap))
        if lewat:
            pesan += ' ' + _('%d dilewati karena statusnya bukan Draft atau Ditolak.', lewat)
        return self._notifikasi_massal(pesan, 'success' if siap else 'warning')

    def action_setujui_massal(self):
        self._cek_operator()
        siap = self.filtered(lambda r: r.state == 'menunggu' and r.mitra_id.active)
        siap.action_setujui()
        lewat = len(self) - len(siap)
        pesan = _('%d anggota disetujui dan menjadi Aktif.', len(siap))
        if lewat:
            pesan += ' ' + _('%d dilewati karena tidak berstatus Menunggu Validasi atau mitranya tidak aktif.', lewat)
        return self._notifikasi_massal(pesan, 'success' if siap else 'warning')

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

    def action_kembali_draft(self):
        self._cek_operator()
        self.write({'state': 'draft'})
