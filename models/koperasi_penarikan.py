from odoo import api, fields, models, _
from odoo.exceptions import UserError

from .koperasi_pinjaman import STATE_AKTIF, rp

JENIS_PENARIKAN = [
    ('sukarela', 'Simpanan Sukarela'),
    ('keluar', 'Keluar dari Keanggotaan'),
]
STATE_PENARIKAN = [
    ('draft', 'Draft'),
    ('diajukan', 'Menunggu Pembayaran'),
    ('dibayar', 'Dibayar'),
    ('ditolak', 'Ditolak'),
]
STATE_EDITABLE = ('draft', 'ditolak')
FIELD_ALUR = {
    'state', 'name', 'tanggal_ajukan', 'tanggal_bayar', 'periode_id', 'pembayar_id', 'simpok', 'simwa',
}


class KoperasiPenarikan(models.Model):
    """Penarikan simpanan anggota.

    - Simpanan Sukarela: sebagian atau seluruh saldo sukarela; anggota tetap aktif.
    - Keluar dari Keanggotaan: seluruh simpanan (pokok, wajib, sukarela) dikembalikan dan
      anggota menjadi Keluar. Syaratnya tidak ada pinjaman, tunggakan, atau tagihan menggantung.
    Diajukan admin mitra, dibayar operator pusat (jurnal: Dr akun simpanan / Cr Bank).
    """
    _name = 'koperasi.penarikan'
    _description = 'Penarikan Simpanan Anggota'
    _inherit = ['mail.thread', 'koperasi.mata.uang.mixin']
    _order = 'id desc'

    name = fields.Char('Nomor', readonly=True, copy=False, index=True)
    anggota_id = fields.Many2one(
        'koperasi.anggota', string='Anggota', required=True, index=True,
        ondelete='restrict', tracking=True, domain="[('state', '=', 'aktif')]")
    mitra_id = fields.Many2one(related='anggota_id.mitra_id', store=True, index=True)
    jenis = fields.Selection(
        JENIS_PENARIKAN, string='Jenis Penarikan', default='sukarela', required=True, tracking=True)
    simpok = fields.Monetary('Simpanan Pokok', readonly=True, copy=False)
    simwa = fields.Monetary('Simpanan Wajib', readonly=True, copy=False)
    simsula = fields.Monetary('Simpanan Sukarela', tracking=True)
    total = fields.Monetary('Jumlah Ditarik', compute='_compute_total', store=True, tracking=True)
    keterangan = fields.Char('Keterangan')

    # Saldo anggota saat ini, sebagai acuan pengisian
    saldo_simpok = fields.Monetary(related='anggota_id.saldo_simpok', string='Saldo Pokok')
    saldo_simwa = fields.Monetary(related='anggota_id.saldo_simwa', string='Saldo Wajib')
    saldo_simsula = fields.Monetary(related='anggota_id.saldo_simsula', string='Saldo Sukarela')
    rekening = fields.Char('Rekening Tujuan', compute='_compute_rekening')

    state = fields.Selection(
        STATE_PENARIKAN, string='Status', default='draft', required=True,
        tracking=True, copy=False, index=True)
    alasan_tolak = fields.Text('Alasan Penolakan', copy=False)
    tanggal_ajukan = fields.Datetime('Tanggal Diajukan', readonly=True, copy=False)
    tanggal_bayar = fields.Date('Tanggal Dibayar', readonly=True, copy=False, index=True)
    periode_id = fields.Many2one(
        'koperasi.periode', string='Periode', readonly=True, copy=False, index=True, ondelete='restrict')
    pembayar_id = fields.Many2one('res.users', string='Dibayar Oleh', readonly=True, copy=False)

    _nominal_wajar = models.Constraint(
        'CHECK(simpok >= 0 AND simwa >= 0 AND simsula >= 0)', 'Nominal penarikan tidak boleh negatif.')

    @api.depends('simpok', 'simwa', 'simsula')
    def _compute_total(self):
        for r in self:
            r.total = r.simpok + r.simwa + r.simsula

    @api.depends('anggota_id.bank_tujuan', 'anggota_id.bank_lainnya',
                 'anggota_id.nomor_rekening', 'anggota_id.atas_nama')
    def _compute_rekening(self):
        for r in self:
            a = r.anggota_id
            r.rekening = '%s %s a.n. %s' % (a._nama_bank(), a.nomor_rekening, a.atas_nama or a.name) \
                if a and a.nomor_rekening else False

    @api.onchange('jenis', 'anggota_id')
    def _onchange_jenis(self):
        if self.jenis == 'keluar' and self.anggota_id:
            self.simsula = self.anggota_id.saldo_simsula

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
        for vals in vals_list:
            if not self.env.su:
                for f in FIELD_ALUR:
                    vals.pop(f, None)
            vals['name'] = self.env['ir.sequence'].sudo().next_by_code('koperasi.penarikan')
        records = super().create(vals_list)
        records._isi_keluar()
        return records

    def write(self, vals):
        if not self.env.su:
            if FIELD_ALUR & vals.keys():
                raise UserError(_('Field tersebut diatur oleh sistem.'))
            if {'anggota_id', 'jenis', 'simsula'} & vals.keys() \
                    and any(r.state not in STATE_EDITABLE for r in self):
                raise UserError(_('Penarikan yang sudah diajukan tidak dapat diubah.'))
        res = super().write(vals)
        if {'anggota_id', 'jenis'} & vals.keys():
            self._isi_keluar()
        return res

    def unlink(self):
        if any(r.state != 'draft' for r in self):
            raise UserError(_('Hanya penarikan berstatus Draft yang dapat dihapus.'))
        return super().unlink()

    # ------------------------------------------------------------------
    # Aturan
    # ------------------------------------------------------------------
    def _isi_keluar(self):
        """Penarikan karena keluar selalu sebesar seluruh saldo; sukarela tanpa pokok/wajib."""
        for r in self.sudo():
            if r.state not in STATE_EDITABLE:
                continue
            a = r.anggota_id
            if r.jenis == 'keluar':
                r.write({'simpok': a.saldo_simpok, 'simwa': a.saldo_simwa, 'simsula': a.saldo_simsula})
            elif r.simpok or r.simwa:
                r.write({'simpok': 0, 'simwa': 0})

    def _cek_layak(self):
        Line = self.env['koperasi.tagihan.line'].sudo()
        for r in self:
            a = r.anggota_id.sudo()
            if a.state != 'aktif':
                raise UserError(_('%s bukan anggota aktif.', a.name))
            lain = self.sudo().search([
                ('anggota_id', '=', a.id), ('state', '=', 'diajukan'), ('id', '!=', r.id)], limit=1)
            if lain:
                raise UserError(_(
                    '%(anggota)s masih punya penarikan %(nomor)s yang menunggu pembayaran.',
                    anggota=a.name, nomor=lain.name))
            if r.jenis == 'sukarela':
                if r.simsula <= 0:
                    raise UserError(_('Isi nominal simpanan sukarela yang ditarik.'))
                if r.currency_id.compare_amounts(r.simsula, a.saldo_simsula) > 0:
                    raise UserError(_(
                        'Penarikan %(tarik)s melebihi saldo simpanan sukarela %(saldo)s.',
                        tarik=rp(r.simsula), saldo=rp(a.saldo_simsula)))
                continue
            # keluar dari keanggotaan
            pinjaman = a.pinjaman_ids.filtered(lambda p: p.state in STATE_AKTIF)
            if pinjaman:
                raise UserError(_(
                    '%(anggota)s masih punya pinjaman %(nomor)s yang belum lunas atau masih diproses. '
                    'Selesaikan dulu sebelum anggota keluar.', anggota=a.name, nomor=pinjaman[0].name))
            if a.tunggakan_total:
                raise UserError(_(
                    '%(anggota)s masih punya tunggakan %(jumlah)s. Lunasi dulu sebelum anggota keluar.',
                    anggota=a.name, jumlah=rp(a.tunggakan_total)))
            menggantung = Line.search([
                ('anggota_id', '=', a.id), ('status', '=', 'menunggu'),
                ('tagihan_id.state', '!=', 'draft')], limit=1)
            if menggantung:
                raise UserError(_(
                    'Tagihan %(tagihan)s untuk %(anggota)s belum divalidasi. Validasi dulu pembayarannya '
                    'supaya saldo simpanan yang dikembalikan sudah final.',
                    tagihan=menggantung.tagihan_id.name, anggota=a.name))
            if not r.total and not a.saldo_total:
                continue  # anggota tanpa saldo tetap boleh keluar

    # ------------------------------------------------------------------
    # Alur
    # ------------------------------------------------------------------
    def action_ajukan(self):
        for r in self:
            if r.state not in STATE_EDITABLE:
                raise UserError(_('Hanya penarikan Draft/Ditolak yang dapat diajukan.'))
            r._isi_keluar()
            r._cek_layak()
            # sudo: admin mitra tidak menulis field alur secara langsung (lihat write())
            r.sudo().write({'state': 'diajukan', 'tanggal_ajukan': fields.Datetime.now(), 'alasan_tolak': False})

    def action_bayar(self):
        """Operator mencatat pembayaran: saldo anggota berkurang dan jurnal dibuat."""
        self._cek_operator()
        hari_ini = fields.Date.context_today(self)
        periode = self.env['koperasi.periode']._untuk_tanggal(hari_ini)
        for r in self:
            if r.state != 'diajukan':
                raise UserError(_('Hanya penarikan berstatus Menunggu Pembayaran yang dapat dibayar.'))
            a = r.anggota_id.sudo()
            if r.jenis == 'keluar':
                # saldo bisa berubah sejak diajukan: pakai saldo terbaru
                r.sudo().write({'simpok': a.saldo_simpok, 'simwa': a.saldo_simwa, 'simsula': a.saldo_simsula})
            r._cek_layak()
            r.sudo().write({
                'state': 'dibayar', 'tanggal_bayar': hari_ini, 'periode_id': periode.id,
                'pembayar_id': self.env.user.id,
            })
            r._buat_jurnal()
            if r.jenis == 'keluar':
                r._keluarkan_anggota()
            r.message_post(body=_(
                'Penarikan dibayar %(jumlah)s ke %(rekening)s.',
                jumlah=rp(r.total), rekening=r.rekening or _('rekening belum diisi')))
        return True

    def _keluarkan_anggota(self):
        self.ensure_one()
        a = self.anggota_id.sudo()
        # baris anggota di tagihan yang masih Draft tidak lagi ditagihkan
        self.env['koperasi.tagihan.line'].sudo().search([
            ('anggota_id', '=', a.id), ('tagihan_id.state', '=', 'draft')]).unlink()
        a.write({'state': 'keluar', 'tanggal_keluar': self.tanggal_bayar})
        a.message_post(body=_(
            'Keluar dari keanggotaan. Seluruh simpanan %(jumlah)s dikembalikan lewat %(nomor)s.',
            jumlah=rp(self.total), nomor=self.name))

    def _buat_jurnal(self):
        Akun = self.env['koperasi.akun']
        for r in self.sudo():
            if not r.total:
                continue
            self.env['koperasi.jurnal']._buat_otomatis({
                'tanggal': r.tanggal_bayar,
                'keterangan': _('Penarikan simpanan %(nomor)s - %(anggota)s', nomor=r.name, anggota=r.anggota_id.name),
                'sumber': 'penarikan',
                'mitra_id': r.mitra_id.id,
                'penarikan_id': r.id,
            }, [
                (Akun._sistem('simpok'), r.simpok, 0),
                (Akun._sistem('simwa'), r.simwa, 0),
                (Akun._sistem('simsula'), r.simsula, 0),
                (Akun._sistem('bank'), 0, r.total),
            ])

    def action_tolak(self):
        self._cek_operator()
        for r in self:
            if r.state != 'diajukan':
                raise UserError(_('Hanya penarikan berstatus Menunggu Pembayaran yang dapat ditolak.'))
            if not r.alasan_tolak:
                raise UserError(_('Alasan penolakan wajib diisi.'))
            r.sudo().write({'state': 'ditolak'})

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


class KoperasiJurnal(models.Model):
    _inherit = 'koperasi.jurnal'

    penarikan_id = fields.Many2one(
        'koperasi.penarikan', string='Penarikan Simpanan', readonly=True, index=True, ondelete='restrict')


class KoperasiAnggota(models.Model):
    _inherit = 'koperasi.anggota'

    penarikan_ids = fields.One2many(
        'koperasi.penarikan', 'anggota_id', string='Penarikan Simpanan', readonly=True)
    penarikan_count = fields.Integer('Penarikan', compute='_compute_penarikan_count')

    def _compute_penarikan_count(self):
        data = self.env['koperasi.penarikan']._read_group(
            [('anggota_id', 'in', self.ids)], ['anggota_id'], ['__count'])
        jumlah = {a.id: n for a, n in data}
        for r in self:
            r.penarikan_count = jumlah.get(r.id, 0)

    def action_lihat_penarikan(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Penarikan Simpanan %s', self.name),
            'res_model': 'koperasi.penarikan',
            'view_mode': 'list,form',
            'domain': [('anggota_id', '=', self.id)],
            'context': {'default_anggota_id': self.id},
        }
