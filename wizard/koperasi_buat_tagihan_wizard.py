from odoo import fields, models, _
from odoo.exceptions import UserError


class KoperasiBuatTagihanWizard(models.TransientModel):
    _name = 'koperasi.buat.tagihan.wizard'
    _description = 'Buat Tagihan'

    periode_id = fields.Many2one(
        'koperasi.periode', string='Periode', required=True,
        default=lambda self: self.env['koperasi.periode']._untuk_tanggal(
            fields.Date.context_today(self)))
    mitra_ids = fields.Many2many(
        'koperasi.mitra', string='Mitra',
        help='Kosongkan untuk membuat tagihan semua mitra.')

    def action_buat(self):
        self.ensure_one()
        if not self.env['koperasi.pengaturan']._get().simwa:
            raise UserError(_('Atur dulu besaran simpanan wajib di menu Pengaturan.'))
        tagihan = self.env['koperasi.tagihan']._buat_tagihan(
            self.periode_id, self.mitra_ids or None)
        if not tagihan:
            raise UserError(_(
                'Tidak ada tagihan baru. Tagihan periode ini sudah dibuat, '
                'atau mitra belum punya anggota aktif.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Tagihan'),
            'res_model': 'koperasi.tagihan',
            'view_mode': 'list,form',
            'domain': [('id', 'in', tagihan.ids)],
        }
