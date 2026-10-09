from odoo import api, fields, models


class KoperasiKontakMixin(models.AbstractModel):
    """Logo/foto, alamat, dan penyalinan satu arah ke res.partner.

    Model pemakai wajib punya field `name`. Kontak dibuat otomatis saat record
    dibuat, lalu field yang berubah disalin setiap kali record ditulis.
    """
    _name = 'koperasi.kontak.mixin'
    _description = 'Data kontak koperasi yang disalin ke res.partner'
    _inherit = ['avatar.mixin']

    partner_id = fields.Many2one(
        'res.partner', string='Kontak', readonly=True, copy=False, ondelete='restrict')
    telepon = fields.Char('Telepon')
    email = fields.Char('Email')
    # Alamat dipecah seperti res.partner agar bisa disalin apa adanya ke kontak.
    street = fields.Char('Jalan')
    street2 = fields.Char('Jalan 2')
    city = fields.Char('Kota')
    state_id = fields.Many2one(
        'res.country.state', string='Provinsi', ondelete='restrict',
        domain="[('country_id', '=?', country_id)]")
    zip = fields.Char('Kode Pos')
    country_id = fields.Many2one(
        'res.country', string='Negara', ondelete='restrict',
        default=lambda self: self.env.ref('base.id', raise_if_not_found=False))

    # Field di luar _partner_field_map() yang ikut memicu penyalinan.
    _partner_sync_extra = ()

    def _partner_field_map(self):
        """Field record ini -> field res.partner."""
        return {
            'name': 'name',
            'telepon': 'phone',
            'email': 'email',
            'street': 'street',
            'street2': 'street2',
            'city': 'city',
            'state_id': 'state_id',
            'zip': 'zip',
            'country_id': 'country_id',
            'image_1920': 'image_1920',
        }

    def _partner_vals(self, fnames=None):
        self.ensure_one()
        vals = {}
        for mf, pf in self._partner_field_map().items():
            if fnames is None or mf in fnames:
                value = self[mf]
                vals[pf] = value.id if self._fields[mf].type == 'many2one' else value
        return vals

    def _partner_vals_baru(self):
        """Nilai tambahan yang hanya dipakai saat kontak dibuat."""
        return {}

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_partner()
        return records

    def write(self, vals):
        res = super().write(vals)
        fnames = (self._partner_field_map().keys() | set(self._partner_sync_extra)) & vals.keys()
        if fnames:
            self._sync_partner(fnames)
        return res

    def _sync_partner(self, fnames=None):
        """Buat kontak bila belum ada, selain itu salin field yang berubah.

        sudo: pengguna koperasi tidak punya hak create/write res.partner, tapi
        kontak ini turunan langsung dari record yang berhak mereka kelola.
        """
        for r in self.sudo():
            if r.partner_id:
                r.partner_id.write(r._partner_vals(fnames))
            else:
                r.partner_id = self.env['res.partner'].sudo().create(
                    dict(r._partner_vals(), **r._partner_vals_baru()))
