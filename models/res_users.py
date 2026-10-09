from odoo import fields, models


class ResUsers(models.Model):
    _inherit = 'res.users'

    # User dengan mitra terisi = admin mitra tsb. Sengaja TIDAK masuk
    # SELF_WRITEABLE_FIELDS, jadi admin mitra tidak bisa memindahkan dirinya
    # ke mitra lain; hanya admin sistem/operator yang mengatur ini.
    koperasi_mitra_id = fields.Many2one(
        'koperasi.mitra', string='Mitra Koperasi', index=True, ondelete='set null',
        help='Mitra yang dikelola user ini (untuk grup Admin Mitra).')
