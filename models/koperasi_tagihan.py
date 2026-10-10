from odoo import api, fields, models, _
from odoo.exceptions import UserError

STATE_TAGIHAN = [
    ('draft', 'Draft'),
    ('terbit', 'Ditagihkan'),
    ('lunas', 'Selesai'),  # semua baris sudah divalidasi; nilai 'lunas' dipertahankan
]

STATUS_BARIS = [
    ('menunggu', 'Belum Divalidasi'),
    ('dibayar', 'Dibayar'),
    ('belum', 'Belum Dibayar'),
]

# Perubahan field ini hanya boleh saat tagihan Draft, karena menentukan nominal tagihan.
FIELD_NOMINAL = {
    'tagihan_id', 'anggota_id', 'simpok', 'simwa', 'simsula', 'tunggakan',
    'angsuran_pokok', 'angsuran_jasa',
}


class KoperasiSimsula(models.Model):
    """Besaran simpanan sukarela per anggota beserta periode mulai berlakunya.

    Dibuat otomatis oleh koperasi.anggota saat field simsula berubah.
    """
    _name = 'koperasi.simsula'
    _description = 'Riwayat Simpanan Sukarela Anggota'
    _order = 'berlaku_mulai desc, id desc'
    _rec_name = 'periode_id'

    anggota_id = fields.Many2one(
        'koperasi.anggota', string='Anggota', required=True, index=True,
        ondelete='cascade')
    periode_id = fields.Many2one(
        'koperasi.periode', string='Berlaku Mulai Periode', required=True,
        ondelete='restrict')
    berlaku_mulai = fields.Date(related='periode_id.tanggal_mulai', store=True)
    currency_id = fields.Many2one(related='anggota_id.currency_id')
    nominal = fields.Monetary('Simpanan Sukarela / Bulan')


class KoperasiTagihan(models.Model):
    _name = 'koperasi.tagihan'
    _description = 'Tagihan Mitra'  # simpanan + angsuran pinjaman
    _inherit = ['mail.thread', 'koperasi.mata.uang.mixin']
    _order = 'periode desc, id desc'

    name = fields.Char('Nomor', readonly=True, copy=False, index=True)
    mitra_id = fields.Many2one(
        'koperasi.mitra', string='Mitra', required=True, index=True,
        ondelete='restrict', readonly=True)
    periode_id = fields.Many2one(
        'koperasi.periode', string='Periode', required=True, readonly=True,
        index=True, ondelete='restrict')
    # tanggal mulai periode, disimpan untuk pengurutan dan perbandingan
    periode = fields.Date(related='periode_id.tanggal_mulai', store=True, index=True)
    periode_state = fields.Selection(related='periode_id.state', string='Status Periode')
    state = fields.Selection(
        STATE_TAGIHAN, string='Status', default='draft', required=True,
        tracking=True, copy=False)
    tanggal_terbit = fields.Date('Tanggal Ditagihkan', readonly=True, copy=False)
    tanggal_bayar = fields.Date('Tanggal Selesai', readonly=True, copy=False)
    catatan = fields.Text('Catatan')

    line_ids = fields.One2many('koperasi.tagihan.line', 'tagihan_id', string='Rincian')
    jumlah_anggota = fields.Integer('Jumlah Anggota', compute='_compute_total', store=True)
    total_simpok = fields.Monetary('Total Simpanan Pokok', compute='_compute_total', store=True)
    total_simwa = fields.Monetary('Total Simpanan Wajib', compute='_compute_total', store=True)
    total_simsula = fields.Monetary('Total Simpanan Sukarela', compute='_compute_total', store=True)
    total_angsuran_pokok = fields.Monetary(
        'Total Angsuran Pokok', compute='_compute_total', store=True)
    total_angsuran_jasa = fields.Monetary(
        'Total Angsuran Jasa', compute='_compute_total', store=True)
    total_tunggakan = fields.Monetary('Total Tunggakan', compute='_compute_total', store=True)
    total = fields.Monetary('Total Tagihan', compute='_compute_total', store=True, tracking=True)
    total_dibayar = fields.Monetary('Sudah Dibayar', compute='_compute_validasi', store=True)
    total_belum = fields.Monetary('Belum Dibayar', compute='_compute_validasi', store=True)
    jumlah_menunggu = fields.Integer('Belum Divalidasi', compute='_compute_validasi', store=True)
    jumlah_belum = fields.Integer('Anggota Belum Membayar', compute='_compute_validasi', store=True)

    _mitra_periode_uniq = models.Constraint(
        'UNIQUE(mitra_id, periode_id)', 'Tagihan mitra ini untuk periode tersebut sudah ada.')

    @api.depends('line_ids.simpok', 'line_ids.simwa', 'line_ids.simsula', 'line_ids.tunggakan',
                 'line_ids.angsuran_pokok', 'line_ids.angsuran_jasa')
    def _compute_total(self):
        for r in self:
            r.jumlah_anggota = len(r.line_ids)
            r.total_simpok = sum(r.line_ids.mapped('simpok'))
            r.total_simwa = sum(r.line_ids.mapped('simwa'))
            r.total_simsula = sum(r.line_ids.mapped('simsula'))
            r.total_angsuran_pokok = sum(r.line_ids.mapped('angsuran_pokok'))
            r.total_angsuran_jasa = sum(r.line_ids.mapped('angsuran_jasa'))
            r.total_tunggakan = sum(r.line_ids.mapped('tunggakan'))
            r.total = (r.total_simpok + r.total_simwa + r.total_simsula
                       + r.total_angsuran_pokok + r.total_angsuran_jasa + r.total_tunggakan)

    @api.depends('line_ids.status', 'line_ids.total')
    def _compute_validasi(self):
        for r in self:
            dibayar = r.line_ids.filtered(lambda l: l.status == 'dibayar')
            belum = r.line_ids.filtered(lambda l: l.status == 'belum')
            r.total_dibayar = sum(dibayar.mapped('total'))
            r.total_belum = sum(belum.mapped('total'))
            r.jumlah_belum = len(belum)
            r.jumlah_menunggu = len(r.line_ids.filtered(lambda l: l.status == 'menunggu'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            periode = self.env['koperasi.periode'].browse(vals['periode_id'])
            if periode.state == 'ditutup':
                raise UserError(_(
                    'Periode %s sudah ditutup, tagihan baru tidak dapat dibuat.', periode.name))
            mitra = self.env['koperasi.mitra'].browse(vals['mitra_id'])
            vals['name'] = 'TGH/%s/%s' % (periode.tanggal_mulai.strftime('%Y%m'), mitra.kode)
        return super().create(vals_list)

    def unlink(self):
        if any(r.state != 'draft' for r in self):
            raise UserError(_('Hanya tagihan berstatus Draft yang dapat dihapus.'))
        return super().unlink()

    # ------------------------------------------------------------------
    # Pembuatan tagihan
    # ------------------------------------------------------------------
    def _siapkan_baris(self):
        """Nilai baris tagihan: satu per anggota aktif mitra pada periode ini."""
        self.ensure_one()
        cfg = self.env['koperasi.pengaturan']._get()
        anggota = self.env['koperasi.anggota'].search([
            ('mitra_id', '=', self.mitra_id.id),
            ('state', '=', 'aktif'),
            ('tanggal_bergabung', '<=', self.periode_id.tanggal_akhir),
        ], order='name')
        # Simpok hanya sekali: lewati anggota yang sudah pernah ditagih simpok.
        # (Yang belum membayar tidak ditagih ulang di sini, tetapi lewat tunggakan.)
        sudah_simpok = self.env['koperasi.tagihan.line'].search([
            ('anggota_id', 'in', anggota.ids),
            ('tagihan_id', '!=', self.id),
            ('simpok', '>', 0),
        ]).anggota_id
        return [{
            'anggota_id': a.id,
            'simpok': 0 if a in sudah_simpok else cfg.simpok,
            'simwa': cfg.simwa,
            'simsula': a._simsula_pada(self.periode),
        } for a in anggota]

    def _isi_baris(self, baris=None):
        """Buat baris rincian lalu muat angsuran pinjaman dan tunggakannya."""
        self.ensure_one()
        baris = self._siapkan_baris() if baris is None else baris
        self.line_ids = [fields.Command.create(vals) for vals in baris]
        self.line_ids._muat_angsuran()
        self.line_ids._hitung_tunggakan()

    @api.model
    def _buat_tagihan(self, periode, mitra=None):
        """Buat tagihan Draft untuk tiap mitra yang belum punya tagihan pada
        periode ini dan punya anggota aktif. Mengembalikan tagihan yang dibuat."""
        mitra = mitra or self.env['koperasi.mitra'].search([])
        sudah = self.search([('periode_id', '=', periode.id), ('mitra_id', 'in', mitra.ids)]).mitra_id
        dibuat = self.browse()
        for m in mitra - sudah:
            tagihan = self.create({'mitra_id': m.id, 'periode_id': periode.id})
            baris = tagihan._siapkan_baris()
            if not baris:
                tagihan.unlink()
                continue
            tagihan._isi_baris(baris)
            dibuat += tagihan
        return dibuat

    @api.model
    def _cron_buat_tagihan(self):
        Periode = self.env['koperasi.periode']
        Periode._pastikan_ke_depan()  # supaya periode setahun ke depan selalu bisa dipilih
        if not self.env['koperasi.pengaturan']._get().simwa:
            return  # besaran belum diatur, jangan terbitkan tagihan kosong
        self._buat_tagihan(Periode._untuk_tanggal(fields.Date.context_today(self)))

    # ------------------------------------------------------------------
    # Alur
    # ------------------------------------------------------------------
    def _cek_state(self, state):
        if any(r.state != state for r in self):
            label = dict(STATE_TAGIHAN)[state]
            raise UserError(_('Aksi ini hanya untuk tagihan berstatus %s.', label))

    def _cek_operator(self):
        if not self.env.user.has_group('koperasi.group_koperasi_operator'):
            raise UserError(_('Hanya operator pusat yang dapat memvalidasi tagihan.'))

    def action_hitung_ulang(self):
        self._cek_state('draft')
        for r in self:
            r.line_ids.unlink()
            r._isi_baris()

    def action_terbitkan(self):
        self._cek_state('draft')
        if any(not r.line_ids for r in self):
            raise UserError(_('Tagihan tanpa rincian tidak dapat ditagihkan.'))
        # Angsuran atau tunggakan baru mungkin muncul sejak tagihan ini dibuat
        # (pinjaman baru cair, periode sebelumnya baru selesai divalidasi),
        # jadi hitung sekali lagi.
        self.line_ids._muat_angsuran()
        self.line_ids._hitung_tunggakan()
        self.write({'state': 'terbit', 'tanggal_terbit': fields.Date.context_today(self)})

    def action_bayar_semua(self):
        """Pintasan: semua baris yang belum divalidasi dianggap dibayar."""
        self._cek_operator()
        self._cek_state('terbit')
        for r in self:
            r.line_ids.filtered(lambda l: l.status == 'menunggu')._validasi('dibayar')

    def action_kembali_draft(self):
        self._cek_state('terbit')
        if any(l.status != 'menunggu' for l in self.line_ids):
            raise UserError(_(
                'Sudah ada anggota yang divalidasi. Batalkan validasinya dulu '
                'sebelum mengembalikan tagihan ke Draft.'))
        self.write({'state': 'draft', 'tanggal_terbit': False})

    def _perbarui_selesai(self):
        """Tagihan otomatis Selesai bila semua baris sudah divalidasi, dan
        kembali Ditagihkan bila ada baris yang validasinya dibatalkan."""
        hari_ini = fields.Date.context_today(self)
        for r in self:
            menunggu = any(l.status == 'menunggu' for l in r.line_ids)
            if r.state == 'terbit' and r.line_ids and not menunggu:
                r.write({'state': 'lunas', 'tanggal_bayar': hari_ini})
            elif r.state == 'lunas' and menunggu:
                r.write({'state': 'terbit', 'tanggal_bayar': False})


class KoperasiTagihanLine(models.Model):
    _name = 'koperasi.tagihan.line'
    _description = 'Rincian Tagihan per Anggota'
    _order = 'tagihan_id desc, id'
    _rec_name = 'anggota_id'

    tagihan_id = fields.Many2one(
        'koperasi.tagihan', string='Tagihan', required=True, index=True,
        ondelete='cascade')
    mitra_id = fields.Many2one(related='tagihan_id.mitra_id', store=True, index=True)
    periode_id = fields.Many2one(related='tagihan_id.periode_id')
    periode = fields.Date(related='tagihan_id.periode')
    state = fields.Selection(related='tagihan_id.state')
    currency_id = fields.Many2one(related='tagihan_id.currency_id')
    anggota_id = fields.Many2one(
        'koperasi.anggota', string='Anggota', required=True, index=True,
        ondelete='restrict', domain="[('mitra_id', '=', mitra_id)]")
    simpok = fields.Monetary('Simpanan Pokok')
    simwa = fields.Monetary('Simpanan Wajib')
    simsula = fields.Monetary('Simpanan Sukarela')
    angsuran_pokok = fields.Monetary(
        'Angsuran Pokok', readonly=True,
        help='Angsuran pokok pinjaman anggota yang jatuh pada periode ini.')
    angsuran_jasa = fields.Monetary(
        'Angsuran Jasa', readonly=True, help='Jasa pinjaman untuk platform pada periode ini.')
    angsuran_ids = fields.One2many(
        'koperasi.pinjaman.angsuran', 'tagihan_line_id', string='Angsuran Pinjaman', readonly=True)
    tunggakan = fields.Monetary(
        'Tunggakan', readonly=True,
        help='Tagihan periode sebelumnya yang belum dibayar anggota ini.')
    total = fields.Monetary('Jumlah', compute='_compute_total', store=True)

    # Validasi pembayaran per anggota
    status = fields.Selection(
        STATUS_BARIS, string='Status Bayar', default='menunggu', required=True,
        readonly=True, copy=False, index=True)
    tanggal_bayar = fields.Date('Tanggal Dibayar', readonly=True, copy=False)
    # Periode pengakuan pembayaran: periode baris ini sendiri bila divalidasi langsung,
    # atau periode baris pembawanya bila dibayar sebagai tunggakan. Laporan menghitung
    # penerimaan menurut periode ini, sehingga angka periode yang sudah lewat tidak berubah.
    periode_akui_id = fields.Many2one(
        'koperasi.periode', string='Diakui pada Periode', readonly=True, copy=False,
        index=True, ondelete='restrict')
    tanggal_akui = fields.Date(
        'Tanggal Pengakuan', related='periode_akui_id.tanggal_mulai', store=True, index=True)
    dibawa_ke_id = fields.Many2one(
        'koperasi.tagihan.line', string='Ditagih Ulang Di', readonly=True, copy=False,
        ondelete='set null', index=True,
        help='Baris tagihan periode berikutnya yang memuat tunggakan ini.')
    tunggakan_dari_ids = fields.One2many(
        'koperasi.tagihan.line', 'dibawa_ke_id', string='Tunggakan Dari', readonly=True)

    _anggota_uniq = models.Constraint(
        'UNIQUE(tagihan_id, anggota_id)', 'Anggota sudah ada di tagihan ini.')

    @api.depends('simpok', 'simwa', 'simsula', 'tunggakan', 'angsuran_pokok', 'angsuran_jasa')
    def _compute_total(self):
        for r in self:
            r.total = (r.simpok + r.simwa + r.simsula
                       + r.angsuran_pokok + r.angsuran_jasa + r.tunggakan)

    # Nominal menentukan saldo simpanan, jadi hanya boleh berubah saat Draft.
    def _cek_draft(self):
        if any(r.tagihan_id.state != 'draft' for r in self):
            raise UserError(_('Rincian hanya dapat diubah saat tagihan berstatus Draft.'))

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._cek_draft()
        return lines

    def write(self, vals):
        if FIELD_NOMINAL & vals.keys():
            self._cek_draft()
        res = super().write(vals)
        if 'status' in vals:
            # pembayaran baris menentukan lunas tidaknya pinjaman
            self.sudo().angsuran_ids.pinjaman_id._perbarui_lunas()
        return res

    def unlink(self):
        self._cek_draft()
        return super().unlink()

    # ------------------------------------------------------------------
    # Angsuran pinjaman
    # ------------------------------------------------------------------
    def _muat_angsuran(self):
        """Tautkan angsuran pinjaman yang jatuh sampai periode ini ke baris.

        Termasuk angsuran periode lalu yang terlewat (mis. pinjaman cair setelah
        tagihan periodenya terbit), supaya tidak ada angsuran yang tak tertagih.
        Hanya untuk tagihan Draft.
        sudo: jadwal angsuran tidak bisa ditulis pengguna secara langsung.
        """
        Angsuran = self.env['koperasi.pinjaman.angsuran'].sudo()
        for r in self.sudo():
            baru = Angsuran.search([
                ('anggota_id', '=', r.anggota_id.id),
                ('tagihan_line_id', '=', False),
                ('pinjaman_id.state', '=', 'berjalan'),
                ('jatuh_tempo', '<=', r.periode),
            ])
            baru.write({'tagihan_line_id': r.id})
            semua = r.angsuran_ids | baru
            r.write({
                'angsuran_pokok': sum(semua.mapped('pokok')),
                'angsuran_jasa': sum(semua.mapped('jasa')),
            })

    # ------------------------------------------------------------------
    # Tunggakan
    # ------------------------------------------------------------------
    def _hitung_tunggakan(self):
        """Muat tagihan periode sebelumnya yang belum dibayar anggota ke baris ini.

        Baris yang belum dibayar dan belum ditagih ulang dibawa ke baris ini
        (rantai: tunggakan yang sudah dibawa sekali ikut terbawa lagi lewat
        total baris pembawanya). Hanya untuk tagihan Draft.
        sudo: baris periode lama milik tagihan yang sudah terbit tidak boleh
        ditulis pengguna, tetapi penautannya bagian dari proses ini.
        """
        for r in self.sudo():
            kandidat = self.sudo().search([
                ('anggota_id', '=', r.anggota_id.id),
                ('status', '=', 'belum'),
                ('dibawa_ke_id', '=', False),
                ('periode', '<', r.periode),
            ])
            asal = (r.tunggakan_dari_ids | kandidat)
            r.tunggakan = sum(asal.mapped('total'))
            r.tunggakan_dari_ids = [fields.Command.set(asal.ids)]

    @api.model
    def _isi_pengakuan(self):
        """Lengkapi periode pengakuan baris Dibayar yang belum memilikinya (data sebelum
        field ini ada). Dipanggil tiap pembaruan modul; tidak mengubah yang sudah terisi."""
        for r in self.sudo().search([('status', '=', 'dibayar'), ('periode_akui_id', '=', False)]):
            akar = r
            while akar.dibawa_ke_id:
                akar = akar.dibawa_ke_id
            r.periode_akui_id = akar.tagihan_id.periode_id

    def _selesaikan_tunggakan(self):
        """Baris ini dibayar: tunggakan yang dibawanya ikut lunas."""
        for r in self.sudo():
            asal = r.tunggakan_dari_ids
            if asal:
                asal.write({'status': 'dibayar', 'tanggal_bayar': r.tanggal_bayar,
                            'periode_akui_id': r.periode_akui_id.id})
                asal._selesaikan_tunggakan()

    def _batalkan_tunggakan(self):
        """Validasi baris ini dibatalkan: tunggakan yang tadi ikut lunas kembali menunggak."""
        for r in self.sudo():
            asal = r.tunggakan_dari_ids.filtered(lambda a: a.status == 'dibayar')
            if asal:
                asal.write({'status': 'belum', 'tanggal_bayar': False, 'periode_akui_id': False})
                asal._batalkan_tunggakan()

    # ------------------------------------------------------------------
    # Validasi pembayaran
    # ------------------------------------------------------------------
    def _validasi(self, status):
        self.tagihan_id._cek_operator()
        hari_ini = fields.Date.context_today(self)
        for r in self:
            if r.tagihan_id.state not in ('terbit', 'lunas'):
                raise UserError(_('Validasi hanya dapat dilakukan setelah tagihan Ditagihkan.'))
            if r.tagihan_id.periode_id.state == 'ditutup':
                raise UserError(_(
                    'Periode %s sudah ditutup, validasi pembayarannya tidak dapat diubah. '
                    'Tagihan yang belum dibayar ditagihkan kembali pada periode berikutnya.',
                    r.tagihan_id.periode_id.name))
            if r.dibawa_ke_id:
                raise UserError(_(
                    '%(anggota)s sudah ditagih ulang pada tagihan %(tagihan)s. '
                    'Ubah statusnya di tagihan tersebut.',
                    anggota=r.anggota_id.name, tagihan=r.dibawa_ke_id.tagihan_id.name))
            r._batalkan_tunggakan()
            r.sudo().write({
                'status': status,
                'tanggal_bayar': hari_ini if status == 'dibayar' else False,
                'periode_akui_id': r.tagihan_id.periode_id.id if status == 'dibayar' else False,
            })
            if status == 'dibayar':
                r._selesaikan_tunggakan()
            elif status == 'belum':
                r.tagihan_id.message_post(body=_(
                    '%(anggota)s belum membayar tagihan ini (%(jumlah)s) dan akan '
                    'ditagihkan kembali pada periode berikutnya.',
                    anggota=r.anggota_id.name,
                    jumlah=r.currency_id.format(r.total)))
        self.tagihan_id._perbarui_selesai()

    def action_dibayar(self):
        self._validasi('dibayar')

    def action_belum_dibayar(self):
        self._validasi('belum')

    def action_batal_validasi(self):
        self._validasi('menunggu')
