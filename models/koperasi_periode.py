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

STATE_PERIODE = [('terbuka', 'Terbuka'), ('ditutup', 'Ditutup')]


class KoperasiPeriode(models.Model):
    """Master periode bulanan: dipakai tagihan, jadwal angsuran, dst.

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
    # Periode yang ditutup terkunci: tagihan dan validasi pembayarannya tidak bisa diubah
    # lagi, sehingga angka laporannya tetap. Pembayaran susulan diakui di periode berikutnya.
    state = fields.Selection(
        STATE_PERIODE, string='Status', default='terbuka', required=True, readonly=True,
        copy=False, index=True)
    ditutup_oleh_id = fields.Many2one('res.users', string='Ditutup Oleh', readonly=True, copy=False)
    tanggal_tutup = fields.Datetime('Ditutup Pada', readonly=True, copy=False)

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
        ):
            jumlah = self.env[model].sudo().with_context(active_test=False).search_count(
                [(field, 'in', self.ids)])
            if jumlah:
                pemakai.append('%d %s' % (jumlah, label))
        return pemakai

    def write(self, vals):
        if {'bulan', 'tahun'} & vals.keys() and any(r.state == 'ditutup' for r in self):
            raise UserError(_('Periode yang sudah ditutup tidak dapat diubah.'))
        if 'state' in vals and not self.env.context.get('koperasi_tutup_periode'):
            raise UserError(_('Status periode hanya berubah lewat tombol Tutup Periode / Buka Kembali.'))
        return super().write(vals)

    # ------------------------------------------------------------------
    # Tutup periode
    # ------------------------------------------------------------------
    def _cek_operator(self):
        if not self.env.user.has_group('koperasi.group_koperasi_operator'):
            raise UserError(_('Hanya operator pusat yang dapat menutup atau membuka periode.'))

    def action_tutup(self):
        """Tutup periode ini beserta semua periode sebelumnya yang masih terbuka.

        Syarat: periodenya sudah berakhir dan semua tagihannya Selesai (setiap anggota
        sudah divalidasi Dibayar atau Belum Dibayar)."""
        self._cek_operator()
        terbuka = self.filtered(lambda p: p.state == 'terbuka')
        if not terbuka:
            return True
        sasaran = self.search([
            ('state', '=', 'terbuka'), ('tanggal_mulai', '<=', max(terbuka.mapped('tanggal_mulai')))],
            order='tanggal_mulai')
        hari_ini = fields.Date.context_today(self)
        berjalan = sasaran.filtered(lambda p: p.tanggal_akhir >= hari_ini)
        if berjalan:
            raise UserError(_(
                'Periode %s belum berakhir. Periode baru dapat ditutup setelah bulannya lewat.',
                berjalan[0].name))
        # sudo: tagihan Draft tidak terlihat semua pengguna, tetapi tetap menghalangi penutupan
        Tagihan = self.env['koperasi.tagihan'].sudo()
        belum = Tagihan.search(
            [('periode_id', 'in', sasaran.ids), ('state', '!=', 'lunas')], order='periode, id')
        if belum:
            label = dict(Tagihan._fields['state'].selection)
            daftar = '\n'.join('- %s (%s)' % (t.name, label[t.state]) for t in belum[:10])
            if len(belum) > 10:
                daftar += '\n' + _('... dan %d tagihan lain', len(belum) - 10)
            raise UserError(_(
                'Periode belum dapat ditutup karena masih ada tagihan yang belum Selesai. '
                'Validasi pembayaran semua anggotanya (Dibayar / Belum Dibayar), atau hapus '
                'tagihan yang masih Draft:\n%s', daftar))
        sasaran.with_context(koperasi_tutup_periode=True).write({
            'state': 'ditutup',
            'ditutup_oleh_id': self.env.user.id,
            'tanggal_tutup': fields.Datetime.now(),
        })
        return True

    def action_buka(self):
        """Buka kembali periode yang terakhir ditutup (untuk membetulkan kekeliruan)."""
        self._cek_operator()
        self.ensure_one()
        if self.state != 'ditutup':
            return True
        kemudian = self.search(
            [('state', '=', 'ditutup'), ('tanggal_mulai', '>', self.tanggal_mulai)],
            order='tanggal_mulai desc', limit=1)
        if kemudian:
            raise UserError(_(
                'Periode %(ini)s tidak dapat dibuka karena %(nanti)s masih ditutup. '
                'Buka dari periode yang paling akhir.', ini=self.name, nanti=kemudian.name))
        self.with_context(koperasi_tutup_periode=True).write({
            'state': 'terbuka', 'ditutup_oleh_id': False, 'tanggal_tutup': False})
        return True

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

        sudo: dipanggil dari alur otomatis (cron pembuatan tagihan) yang
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
