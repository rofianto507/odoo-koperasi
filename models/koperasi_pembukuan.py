"""Pembukuan sederhana koperasi: bagan akun, jurnal, dan baris jurnal (buku besar).

Jurnal otomatis dibuat dari kejadian operasional:
- pencairan pinjaman   : Dr Piutang Pinjaman / Cr Bank, Cr Pendapatan Biaya Administrasi
- penerimaan tagihan   : Dr Bank / Cr Piutang Pinjaman, Pendapatan Jasa
  (satu jurnal per tagihan, berisi semua baris yang DIAKUI pada periode tagihan itu, termasuk
  tunggakan periode lalu yang ikut lunas; lihat `periode_akui_id` di koperasi.tagihan.line)

Basis kas: angsuran dan jasa diakui saat pembayarannya divalidasi. Transaksi lain
(setoran modal, biaya operasional, dst.) dicatat operator lewat Jurnal Umum.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError

JENIS_AKUN = [
    ('aset', 'Aset'),
    ('kewajiban', 'Kewajiban'),
    ('ekuitas', 'Ekuitas'),
    ('pendapatan', 'Pendapatan'),
    ('beban', 'Beban'),
]
# Akun yang saldo normalnya di debit; selebihnya di kredit.
SALDO_DEBIT = ('aset', 'beban')
ARUS_KAS = [
    ('operasi', 'Aktivitas Operasi'),
    ('investasi', 'Aktivitas Investasi'),
    ('pendanaan', 'Aktivitas Pendanaan'),
]
ARUS_BAWAAN = {
    'aset': 'investasi', 'kewajiban': 'pendanaan', 'ekuitas': 'pendanaan',
    'pendapatan': 'operasi', 'beban': 'operasi',
}
SUMBER_JURNAL = [
    ('manual', 'Jurnal Umum'),
    ('pencairan', 'Pencairan Pinjaman'),
    ('penerimaan', 'Penerimaan Tagihan'),
    ('penutup', 'Jurnal Penutup'),
    ('shu', 'Pembayaran Pembagian Laba'),
]
STATE_JURNAL = [('draft', 'Draft'), ('posted', 'Terposting')]

# Field jurnal yang hanya boleh diubah oleh alur sistem (posting, jurnal otomatis).
FIELD_SISTEM = {'name', 'state', 'sumber', 'periode_id', 'pinjaman_id', 'tagihan_id', 'shu_id'}
# Isi jurnal: hanya boleh diubah selama jurnal manual masih Draft.
FIELD_ISI = {'tanggal', 'keterangan', 'mitra_id', 'line_ids'}


class KoperasiAkun(models.Model):
    _name = 'koperasi.akun'
    _description = 'Akun Pembukuan Koperasi'
    _inherit = ['koperasi.mata.uang.mixin']
    _order = 'kode'
    _rec_names_search = ['kode', 'name']

    kode = fields.Char('Kode', required=True, index=True)
    name = fields.Char('Nama Akun', required=True)
    jenis = fields.Selection(JENIS_AKUN, string='Jenis', required=True)
    kas = fields.Boolean(
        'Akun Kas/Bank', help='Akun tempat uang tunai atau saldo bank dicatat. Dipakai laporan Arus Kas.')
    arus_kas = fields.Selection(
        ARUS_KAS, string='Kelompok Arus Kas', compute='_compute_arus_kas', store=True,
        readonly=False, required=True, precompute=True,
        help='Kelompok di laporan Arus Kas untuk uang masuk/keluar yang lawannya akun ini.')
    active = fields.Boolean(default=True)
    sistem = fields.Boolean(
        'Akun Sistem', readonly=True, copy=False,
        help='Dipakai jurnal otomatis; tidak dapat dihapus atau diubah jenisnya.')
    keterangan = fields.Char('Keterangan')
    saldo = fields.Monetary('Saldo', compute='_compute_saldo')

    _kode_uniq = models.Constraint('UNIQUE(kode)', 'Kode akun sudah dipakai.')

    @api.depends('jenis')
    def _compute_arus_kas(self):
        for r in self:
            r.arus_kas = ARUS_BAWAAN.get(r.jenis, 'operasi')

    @api.depends('kode', 'name')
    def _compute_display_name(self):
        for r in self:
            r.display_name = '%s %s' % (r.kode or '', r.name or '')

    def _compute_saldo(self):
        data = self.env['koperasi.jurnal.line']._read_group(
            [('akun_id', 'in', self.ids), ('state', '=', 'posted')],
            ['akun_id'], ['debit:sum', 'kredit:sum'])
        nilai = {akun.id: (d or 0) - (k or 0) for akun, d, k in data}
        for r in self:
            selisih = nilai.get(r.id, 0)
            r.saldo = selisih if r.jenis in SALDO_DEBIT else -selisih

    @api.constrains('kas', 'jenis')
    def _cek_kas(self):
        for r in self:
            if r.kas and r.jenis != 'aset':
                raise ValidationError(_('Akun kas/bank harus berjenis Aset.'))

    def write(self, vals):
        if not self.env.su and {'jenis', 'kas', 'sistem'} & vals.keys() and any(r.sistem for r in self):
            raise UserError(_('Jenis akun sistem tidak dapat diubah karena dipakai jurnal otomatis.'))
        return super().write(vals)

    def unlink(self):
        if any(r.sistem for r in self):
            raise UserError(_('Akun sistem tidak dapat dihapus.'))
        if self.env['koperasi.jurnal.line'].sudo().search_count([('akun_id', 'in', self.ids)], limit=1):
            raise UserError(_('Akun yang sudah dipakai di jurnal tidak dapat dihapus. Arsipkan saja.'))
        return super().unlink()

    @api.model
    def _sistem(self, kunci):
        """Akun bawaan untuk jurnal otomatis: bank, piutang, jasa, admin."""
        return self.env.ref('koperasi.akun_%s' % kunci).sudo()


class KoperasiJurnal(models.Model):
    _name = 'koperasi.jurnal'
    _description = 'Jurnal Pembukuan Koperasi'
    _inherit = ['mail.thread', 'koperasi.mata.uang.mixin']
    _order = 'tanggal desc, id desc'

    name = fields.Char('Nomor', readonly=True, copy=False, default='/', index=True)
    tanggal = fields.Date(
        'Tanggal', required=True, index=True, tracking=True,
        default=lambda self: fields.Date.context_today(self))
    periode_id = fields.Many2one(
        'koperasi.periode', string='Periode', readonly=True, index=True, ondelete='restrict')
    keterangan = fields.Char('Keterangan', required=True, tracking=True)
    sumber = fields.Selection(
        SUMBER_JURNAL, string='Sumber', default='manual', required=True, readonly=True, index=True)
    state = fields.Selection(
        STATE_JURNAL, string='Status', default='draft', required=True, readonly=True,
        copy=False, tracking=True, index=True)
    mitra_id = fields.Many2one(
        'koperasi.mitra', string='Mitra', index=True, ondelete='restrict',
        help='Opsional. Jurnal bertanda mitra ikut terhitung saat laporan disaring per mitra.')
    pinjaman_id = fields.Many2one(
        'koperasi.pinjaman', string='Pinjaman', readonly=True, index=True, ondelete='restrict')
    tagihan_id = fields.Many2one(
        'koperasi.tagihan', string='Tagihan', readonly=True, index=True, ondelete='restrict')
    line_ids = fields.One2many('koperasi.jurnal.line', 'jurnal_id', string='Rincian', copy=True)
    total_debit = fields.Monetary('Total Debit', compute='_compute_total', store=True)
    total_kredit = fields.Monetary('Total Kredit', compute='_compute_total', store=True)

    @api.depends('line_ids.debit', 'line_ids.kredit')
    def _compute_total(self):
        for r in self:
            r.total_debit = sum(r.line_ids.mapped('debit'))
            r.total_kredit = sum(r.line_ids.mapped('kredit'))

    # ------------------------------------------------------------------
    # create / write / unlink guard
    # ------------------------------------------------------------------
    def _alur_sistem(self):
        """True bila dipanggil dari tombol posting atau pembuat jurnal otomatis."""
        return bool(self.env.context.get('koperasi_jurnal'))

    def _nomor_baru(self, tanggal):
        # ir_sequence_date: tahun pada nomor mengikuti tanggal jurnal, bukan tanggal pembuatannya
        return self.env['ir.sequence'].sudo().with_context(
            ir_sequence_date=fields.Date.to_date(tanggal)).next_by_code('koperasi.jurnal')

    @api.model_create_multi
    def create(self, vals_list):
        Periode = self.env['koperasi.periode']
        sistem = self._alur_sistem()
        for vals in vals_list:
            if not sistem:
                # pengguna hanya membuat jurnal umum berstatus Draft
                for f in FIELD_SISTEM:
                    vals.pop(f, None)
            tanggal = fields.Date.to_date(vals.get('tanggal')) or fields.Date.context_today(self)
            vals['periode_id'] = Periode._untuk_tanggal(tanggal).id
            if vals.get('state') == 'posted' and vals.get('name', '/') == '/':
                vals['name'] = self._nomor_baru(tanggal)
        return super(KoperasiJurnal, self.with_context(koperasi_jurnal_baru=True)).create(vals_list)

    def write(self, vals):
        if not self._alur_sistem():
            if FIELD_SISTEM & vals.keys():
                raise UserError(_('Nomor, status, dan sumber jurnal diatur oleh sistem.'))
            if FIELD_ISI & vals.keys():
                self._cek_bisa_diubah()
        if 'tanggal' in vals:
            vals = dict(vals, periode_id=self.env['koperasi.periode']._untuk_tanggal(vals['tanggal']).id)
        return super().write(vals)

    def _cek_bisa_diubah(self):
        for r in self:
            if r.sumber != 'manual':
                raise UserError(_(
                    'Jurnal %s dibuat otomatis dari transaksi dan tidak dapat diubah. '
                    'Ubah transaksi asalnya, atau buat jurnal koreksi.', r.name))
            if r.state != 'draft':
                raise UserError(_('Jurnal %s sudah diposting. Kembalikan ke Draft untuk mengubahnya.', r.name))

    def unlink(self):
        if not self._alur_sistem():
            self._cek_bisa_diubah()
        return super().unlink()

    # ------------------------------------------------------------------
    # Alur jurnal umum
    # ------------------------------------------------------------------
    def _cek_operator(self):
        if not self.env.user.has_group('koperasi.group_koperasi_operator'):
            raise UserError(_('Hanya operator pusat yang dapat mengelola jurnal.'))

    def _cek_periode_terbuka(self):
        for r in self:
            if r.periode_id.state == 'ditutup':
                raise UserError(_(
                    'Periode %s sudah ditutup. Catat jurnal ini pada periode yang masih terbuka.',
                    r.periode_id.name))

    def _cek_seimbang(self):
        mata_uang = self.env.company.currency_id
        for r in self:
            baris = r.line_ids.filtered(lambda l: l.debit or l.kredit)
            if len(baris) < 2:
                raise UserError(_('Jurnal membutuhkan paling sedikit dua baris yang bernilai.'))
            if mata_uang.compare_amounts(r.total_debit, r.total_kredit) != 0:
                raise UserError(_(
                    'Jurnal belum seimbang: total debit %(debit)s, total kredit %(kredit)s.',
                    debit=mata_uang.format(r.total_debit), kredit=mata_uang.format(r.total_kredit)))

    def action_posting(self):
        self._cek_operator()
        for r in self:
            if r.state != 'draft':
                continue
            r._cek_periode_terbuka()
            r._cek_seimbang()
            nilai = {'state': 'posted'}
            if r.name == '/':
                nilai['name'] = r._nomor_baru(r.tanggal)
            r.with_context(koperasi_jurnal=True).write(nilai)
        return True

    def action_draft(self):
        self._cek_operator()
        for r in self:
            if r.state != 'posted':
                continue
            if r.sumber != 'manual':
                raise UserError(_('Jurnal otomatis tidak dapat dikembalikan ke Draft.'))
            r._cek_periode_terbuka()
            r.with_context(koperasi_jurnal=True).write({'state': 'draft'})
        return True

    # ------------------------------------------------------------------
    # Jurnal otomatis
    # ------------------------------------------------------------------
    @api.model
    def _buat_otomatis(self, nilai, baris):
        """Buat jurnal terposting. baris = [(akun, debit, kredit)], yang nol dilewati."""
        nilai = dict(nilai, state='posted', line_ids=self._perintah_baris(baris))
        return self.sudo().with_context(koperasi_jurnal=True).create(nilai)

    @api.model
    def _perintah_baris(self, baris):
        return [fields.Command.create({'akun_id': akun.id, 'debit': debit, 'kredit': kredit})
                for akun, debit, kredit in baris if debit or kredit]

    @api.model
    def _buat_jurnal_lama(self):
        """Lengkapi jurnal otomatis untuk transaksi yang belum memilikinya (data sebelum
        pembukuan ada). Dipanggil tiap pembaruan modul; yang sudah berjurnal dilewati.
        Dibuat urut tanggal supaya nomor jurnalnya kronologis."""
        Jurnal = self.sudo()
        sudah_p = Jurnal.search([('sumber', '=', 'pencairan')]).pinjaman_id
        sudah_t = Jurnal.search([('sumber', '=', 'penerimaan')]).tagihan_id
        pinjaman = self.env['koperasi.pinjaman'].sudo().search(
            [('tanggal_cair', '!=', False), ('id', 'not in', sudah_p.ids)])
        tagihan = self.env['koperasi.tagihan'].sudo().search(
            [('state', '!=', 'draft'), ('id', 'not in', sudah_t.ids)])
        antrean = [(p.tanggal_cair, 0, p.id, p) for p in pinjaman]
        for t in tagihan:
            baris = t._baris_diakui()
            if baris:
                antrean.append((t._tanggal_penerimaan(baris), 1, t.id, t))
        for _tanggal, jenis, _id, rec in sorted(antrean, key=lambda x: x[:3]):
            if jenis == 0:
                rec._buat_jurnal_pencairan()
            else:
                rec._sinkron_jurnal()


class KoperasiJurnalLine(models.Model):
    _name = 'koperasi.jurnal.line'
    _description = 'Baris Jurnal (Buku Besar)'
    _order = 'tanggal desc, jurnal_id desc, id'
    _rec_name = 'akun_id'

    jurnal_id = fields.Many2one(
        'koperasi.jurnal', string='Jurnal', required=True, index=True, ondelete='cascade')
    akun_id = fields.Many2one(
        'koperasi.akun', string='Akun', required=True, index=True, ondelete='restrict')
    keterangan = fields.Char('Keterangan Baris')
    currency_id = fields.Many2one(related='jurnal_id.currency_id')
    debit = fields.Monetary('Debit')
    kredit = fields.Monetary('Kredit')
    # disimpan agar buku besar dan laporan bisa disaring serta dijumlah langsung
    tanggal = fields.Date(related='jurnal_id.tanggal', store=True, index=True)
    periode_id = fields.Many2one(related='jurnal_id.periode_id', store=True, index=True)
    mitra_id = fields.Many2one(related='jurnal_id.mitra_id', store=True, index=True)
    state = fields.Selection(related='jurnal_id.state', store=True, index=True)
    sumber = fields.Selection(related='jurnal_id.sumber', store=True)
    keterangan_jurnal = fields.Char(related='jurnal_id.keterangan', string='Keterangan')
    jenis_akun = fields.Selection(related='akun_id.jenis', store=True, string='Jenis Akun')

    _nilai_wajar = models.Constraint(
        'CHECK(debit >= 0 AND kredit >= 0 AND NOT (debit > 0 AND kredit > 0))',
        'Satu baris jurnal hanya boleh berisi debit atau kredit, dan tidak boleh negatif.')

    def _cek_jurnal(self):
        if not self.env.context.get('koperasi_jurnal'):
            self.jurnal_id._cek_bisa_diubah()

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('koperasi_jurnal_baru'):
            lines._cek_jurnal()
        return lines

    def write(self, vals):
        self._cek_jurnal()
        return super().write(vals)

    def unlink(self):
        self._cek_jurnal()
        return super().unlink()


# ----------------------------------------------------------------------
# Kaitan ke transaksi operasional
# ----------------------------------------------------------------------
class KoperasiPinjaman(models.Model):
    _inherit = 'koperasi.pinjaman'

    def _buat_jurnal_pencairan(self):
        Akun = self.env['koperasi.akun']
        Jurnal = self.env['koperasi.jurnal']
        for r in self.sudo():
            if not r.tanggal_cair or Jurnal.sudo().search_count(
                    [('pinjaman_id', '=', r.id), ('sumber', '=', 'pencairan')], limit=1):
                continue
            Jurnal._buat_otomatis({
                'tanggal': r.tanggal_cair,
                'keterangan': _('Pencairan pinjaman %(nomor)s - %(anggota)s', nomor=r.name, anggota=r.anggota_id.name),
                'sumber': 'pencairan',
                'mitra_id': r.mitra_id.id,
                'pinjaman_id': r.id,
            }, [
                (Akun._sistem('piutang'), r.pokok, 0),
                (Akun._sistem('bank'), 0, r.pokok - r.biaya_admin),
                (Akun._sistem('admin'), 0, r.biaya_admin),
            ])

    def action_cairkan(self):
        res = super().action_cairkan()
        self._buat_jurnal_pencairan()
        return res


class KoperasiTagihan(models.Model):
    _inherit = 'koperasi.tagihan'

    def _baris_diakui(self):
        """Baris Dibayar yang penerimaannya diakui pada periode tagihan ini: baris tagihan ini
        sendiri dan tunggakan periode lalu yang ikut lunas lewat tagihan ini."""
        self.ensure_one()
        return self.env['koperasi.tagihan.line'].sudo().search([
            ('mitra_id', '=', self.mitra_id.id),
            ('periode_akui_id', '=', self.periode_id.id),
            ('status', '=', 'dibayar'),
        ])

    def _tanggal_penerimaan(self, baris):
        """Tanggal validasi terakhir, dijaga tetap di dalam periode tagihan."""
        self.ensure_one()
        p = self.periode_id
        terakhir = max(baris.filtered('tanggal_bayar').mapped('tanggal_bayar') or [p.tanggal_akhir])
        return min(max(terakhir, p.tanggal_mulai), p.tanggal_akhir)

    def _sinkron_jurnal(self):
        """Samakan jurnal penerimaan tagihan dengan baris yang sudah divalidasi Dibayar."""
        Akun = self.env['koperasi.akun']
        Jurnal = self.env['koperasi.jurnal'].sudo().with_context(koperasi_jurnal=True)
        for t in self.sudo():
            baris = t._baris_diakui()
            jurnal = Jurnal.search([('tagihan_id', '=', t.id), ('sumber', '=', 'penerimaan')], limit=1)
            jumlah = {k: sum(baris.mapped(k)) for k in ('angsuran_pokok', 'angsuran_jasa')}
            total = sum(jumlah.values())
            if not total:
                jurnal.unlink()
                continue
            rincian = [
                (Akun._sistem('bank'), total, 0),
                (Akun._sistem('piutang'), 0, jumlah['angsuran_pokok']),
                (Akun._sistem('jasa'), 0, jumlah['angsuran_jasa']),
            ]
            nilai = {
                'tanggal': t._tanggal_penerimaan(baris),
                'keterangan': _('Penerimaan tagihan %(nomor)s - %(mitra)s', nomor=t.name, mitra=t.mitra_id.name),
            }
            if jurnal:
                jurnal.write(dict(nilai, line_ids=[fields.Command.clear()] + Jurnal._perintah_baris(rincian)))
            else:
                Jurnal._buat_otomatis(dict(nilai, sumber='penerimaan', mitra_id=t.mitra_id.id, tagihan_id=t.id), rincian)


class KoperasiTagihanLine(models.Model):
    _inherit = 'koperasi.tagihan.line'

    def _validasi(self, status):
        res = super()._validasi(status)
        # tunggakan yang ikut lunas/batal diakui pada periode tagihan ini juga, jadi cukup tagihan ini
        self.tagihan_id._sinkron_jurnal()
        return res


class KoperasiPeriode(models.Model):
    _inherit = 'koperasi.periode'

    def action_tutup(self):
        terbuka = self.filtered(lambda p: p.state == 'terbuka')
        if terbuka:
            # sudo: pemeriksaan harus melihat semua jurnal
            draft = self.env['koperasi.jurnal'].sudo().search([
                ('state', '=', 'draft'), ('periode_id.state', '=', 'terbuka'),
                ('periode_id.tanggal_mulai', '<=', max(terbuka.mapped('tanggal_mulai')))], order='tanggal')
            if draft:
                raise UserError(_(
                    'Periode belum dapat ditutup karena masih ada %(n)d jurnal berstatus Draft '
                    '(tertua tanggal %(tgl)s). Posting atau hapus jurnal tersebut dulu.',
                    n=len(draft), tgl=draft[0].tanggal.strftime('%d/%m/%Y')))
        return super().action_tutup()
