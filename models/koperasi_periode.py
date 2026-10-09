from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

BULAN = [
    ('1', 'Januari'), ('2', 'Februari'), ('3', 'Maret'), ('4', 'April'),
    ('5', 'Mei'), ('6', 'Juni'), ('7', 'Juli'), ('8', 'Agustus'),
    ('9', 'September'), ('10', 'Oktober'), ('11', 'November'), ('12', 'Desember'),
]

# Singkatan untuk label periode: JAN 2026, AGU 2026, dst.
BULAN_SINGKAT = {
    '1': 'JAN', '2': 'FEB', '3': 'MAR', '4': 'APR', '5': 'MEI', '6': 'JUN',
    '7': 'JUL', '8': 'AGU', '9': 'SEP', '10': 'OKT', '11': 'NOV', '12': 'DES',
}


class KoperasiPeriode(models.Model):
    """Master periode bulanan: dipakai tagihan, berlaku mulai simsula, dst.

    Cukup isi bulan dan tahun; nama serta tanggal mulai/akhir dihitung.
    """
    _name = 'koperasi.periode'
    _description = 'Periode Koperasi'
    _order = 'tanggal_mulai desc'

    name = fields.Char('Periode', compute='_compute_rentang', store=True)
    bulan = fields.Selection(BULAN, required=True)
    tahun = fields.Integer(
        required=True, default=lambda self: fields.Date.context_today(self).year)
    tanggal_mulai = fields.Date('Tanggal Mulai', compute='_compute_rentang', store=True, index=True)
    tanggal_akhir = fields.Date('Tanggal Akhir', compute='_compute_rentang', store=True)

    _bulan_tahun_uniq = models.Constraint(
        'UNIQUE(bulan, tahun)', 'Periode untuk bulan dan tahun tersebut sudah ada.')
    _tahun_wajar = models.Constraint(
        'CHECK(tahun BETWEEN 2000 AND 2100)', 'Tahun periode tidak valid.')

    @api.depends('bulan', 'tahun')
    def _compute_rentang(self):
        for r in self:
            if r.bulan and 1 <= r.tahun <= 9999:
                r.tanggal_mulai = date(r.tahun, int(r.bulan), 1)
                r.tanggal_akhir = r.tanggal_mulai + relativedelta(months=1, days=-1)
                r.name = '%s %s' % (BULAN_SINGKAT[r.bulan], r.tahun)
            else:
                r.tanggal_mulai = r.tanggal_akhir = r.name = False

    def _dipakai_oleh(self):
        """Nama dokumen yang masih memakai periode-periode ini (kosong bila bebas).

        sudo: pemeriksaan harus melihat semua data, bukan hanya milik pengguna.
        """
        pemakai = []
        for model, field, label in (
            ('koperasi.tagihan', 'periode_id', _('tagihan')),
            ('koperasi.simsula', 'periode_id', _('riwayat simpanan sukarela')),
            ('koperasi.anggota', 'simsula_periode_id', _('anggota')),
        ):
            jumlah = self.env[model].sudo().with_context(active_test=False).search_count(
                [(field, 'in', self.ids)])
            if jumlah:
                pemakai.append('%d %s' % (jumlah, label))
        return pemakai

    def unlink(self):
        pemakai = self._dipakai_oleh()
        if pemakai:
            raise UserError(_(
                'Periode tidak dapat dihapus karena masih dipakai oleh: %s.',
                ', '.join(pemakai)))
        return super().unlink()

    @api.model
    def _untuk_tanggal(self, tanggal):
        """Periode yang memuat tanggal; dibuat bila belum ada.

        sudo: dipanggil dari alur otomatis (cron, pencatatan simsula) yang
        penggunanya belum tentu berhak membuat master periode.
        """
        tanggal = fields.Date.to_date(tanggal)
        Periode = self.sudo()
        periode = Periode.search(
            [('bulan', '=', str(tanggal.month)), ('tahun', '=', tanggal.year)], limit=1)
        if not periode:
            periode = Periode.create({'bulan': str(tanggal.month), 'tahun': tanggal.year})
        return periode.sudo(False)

    @api.model
    def _pastikan_ke_depan(self, jumlah=12, mulai=None):
        """Pastikan periode `jumlah` bulan sejak `mulai` (bawaan: bulan ini) tersedia."""
        mulai = (mulai or fields.Date.context_today(self)).replace(day=1)
        for i in range(jumlah):
            self._untuk_tanggal(mulai + relativedelta(months=i))

    def action_buat_12_bulan(self):
        """Tombol di daftar periode: tambah 12 bulan setelah periode terakhir."""
        terakhir = self.search([], order='tanggal_mulai desc', limit=1)
        self._pastikan_ke_depan(
            mulai=terakhir.tanggal_mulai + relativedelta(months=1) if terakhir else None)
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
