from odoo import fields, models


class KoperasiTolakWizard(models.TransientModel):
    _name = 'koperasi.tolak.wizard'
    _description = 'Alasan Penolakan Anggota Koperasi'

    # res_model/res_id generik agar wizard bisa dipakai ulang oleh model lain
    # (mis. pengajuan pinjaman) di tahap berikutnya.
    res_model = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    alasan_tolak = fields.Text('Alasan Penolakan', required=True)

    def action_konfirmasi(self):
        self.ensure_one()
        record = self.env[self.res_model].browse(self.res_id)
        record.alasan_tolak = self.alasan_tolak
        record.action_tolak()
        return {'type': 'ir.actions.act_window_close'}
