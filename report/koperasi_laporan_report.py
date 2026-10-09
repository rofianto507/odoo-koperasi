import base64

from odoo import api, models
from odoo.tools import file_open

from odoo.addons.koperasi.models.koperasi_laporan_xlsx import _warna_status


def format_nilai(nilai, tipe):
    """Pemformat angka gaya Indonesia (titik ribuan, koma desimal) untuk PDF."""
    if nilai is None or nilai == '':
        return ''
    if tipe == 'rp':
        teks = '{:,.0f}'.format(abs(nilai)).replace(',', '.')
        return ('-Rp %s' % teks) if nilai < 0 else 'Rp %s' % teks
    if tipe == 'angka':
        return '{:,.0f}'.format(nilai).replace(',', '.')
    if tipe == 'persen':
        return ('{:.1f}'.format(nilai)).replace('.', ',') + '%'
    if tipe == 'tanggal':
        try:
            tahun, bulan, hari = str(nilai).split('-')
            return '%s/%s/%s' % (hari, bulan, tahun)
        except ValueError:
            return str(nilai)
    return str(nilai)


class ReportLaporanPdf(models.AbstractModel):
    _name = 'report.koperasi.laporan_pdf'
    _description = 'Laporan Koperasi (PDF)'

    @api.model
    def _get_report_values(self, docids, data=None):
        p = (data or {}).get('params', {})
        laporan = self.env['koperasi.laporan'].get_laporan(
            p.get('jenis', 'ringkasan'), p.get('mitra') or False, p.get('dari') or False,
            p.get('sampai') or False, p.get('anggota') or False)
        with file_open('koperasi/static/src/img/logo.png', 'rb') as f:
            logo = base64.b64encode(f.read()).decode()
        return {
            'doc_ids': [], 'doc_model': 'koperasi.laporan', 'docs': [],
            'laporan': laporan, 'fmt': format_nilai, 'warna_status': _warna_status, 'logo': logo,
        }
