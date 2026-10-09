from odoo import fields, models


class KoperasiAnggotaGaji(models.Model):
    """Satu baris per perubahan gaji; dibuat otomatis oleh koperasi.anggota."""
    _name = 'koperasi.anggota.gaji'
    _description = 'Riwayat Gaji Anggota Koperasi'
    _order = 'tanggal_berlaku desc, id desc'
    _rec_name = 'tanggal_berlaku'

    anggota_id = fields.Many2one(
        'koperasi.anggota', string='Anggota', required=True, index=True,
        ondelete='cascade')
    tanggal_berlaku = fields.Date('Tanggal Berlaku', required=True)
    currency_id = fields.Many2one(related='anggota_id.currency_id')
    gaji_bersih = fields.Monetary('Gaji Bersih')
    perubahan = fields.Monetary(
        'Nominal Perubahan',
        help='Selisih terhadap gaji bersih sebelumnya; 0 untuk catatan pertama.')
