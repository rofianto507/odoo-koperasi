from odoo import api, fields, models


class KoperasiMataUangMixin(models.AbstractModel):
    _name = 'koperasi.mata.uang.mixin'
    _description = 'Mata uang perusahaan untuk field nominal koperasi'

    currency_id = fields.Many2one(
        'res.currency', string='Mata Uang', compute='_compute_currency_id')

    def _compute_currency_id(self):
        self.currency_id = self.env.company.currency_id


class KoperasiPengaturan(models.Model):
    """Satu record saja (koperasi.pengaturan_utama), diatur operator pusat."""
    _name = 'koperasi.pengaturan'
    _description = 'Pengaturan Koperasi'
    _inherit = ['mail.thread', 'koperasi.mata.uang.mixin']

    name = fields.Char(default='Pengaturan Simpanan', required=True, readonly=True)
    simpok = fields.Monetary(
        'Simpanan Pokok', tracking=True,
        help='Ditagihkan satu kali pada tagihan pertama setelah anggota aktif.')
    simwa = fields.Monetary(
        'Simpanan Wajib / Bulan', tracking=True,
        help='Ditagihkan setiap bulan ke semua anggota aktif.')

    batas_potongan = fields.Float(
        'Batas Potongan Gaji (%)', digits=(5, 2), default=40.0, required=True, tracking=True,
        help='Bagian gaji bersih anggota yang paling banyak boleh dipotong tiap bulan, '
             'sudah termasuk simpanan pokok, wajib, dan sukarela. Sisanya menjadi '
             'angsuran maksimum pinjaman.')
    plafon_kelipatan = fields.Monetary(
        'Pembulatan Plafon', default=1000000, required=True, tracking=True,
        help='Plafon pinjaman dibulatkan ke bawah per kelipatan ini.')

    _nominal_positif = models.Constraint(
        'CHECK(simpok >= 0 AND simwa >= 0)', 'Besaran simpanan tidak boleh negatif.')
    _batas_potongan_wajar = models.Constraint(
        'CHECK(batas_potongan > 0 AND batas_potongan <= 100)',
        'Batas potongan gaji harus antara 0 dan 100 persen.')
    _plafon_kelipatan_positif = models.Constraint(
        'CHECK(plafon_kelipatan > 0)', 'Pembulatan plafon harus lebih dari nol.')

    @api.model
    def _get(self):
        return self.env.ref('koperasi.pengaturan_utama').sudo()
