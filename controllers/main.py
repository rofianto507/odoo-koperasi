from odoo import http
from odoo.exceptions import AccessError, UserError
from odoo.http import content_disposition, request

XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'


class KoperasiTagihanController(http.Controller):

    @http.route('/koperasi/tagihan/<int:tagihan_id>/xlsx', type='http', auth='user')
    def unduh_tagihan_xlsx(self, tagihan_id, **kw):
        # Tanpa sudo: record rule menentukan siapa yang boleh mengunduh
        # (admin mitra hanya tagihan mitranya yang sudah ditagihkan).
        tagihan = request.env['koperasi.tagihan'].browse(tagihan_id).exists()
        if not tagihan:
            raise request.not_found()
        try:
            isi = tagihan._buat_xlsx()
        except AccessError:
            raise request.not_found()
        return request.make_response(isi, headers=[
            ('Content-Type', XLSX_MIME),
            ('Content-Disposition', content_disposition(tagihan._nama_file_xlsx())),
            ('Content-Length', len(isi)),
        ])


class KoperasiLaporanController(http.Controller):
    """Unduhan laporan. Semua angka dihitung dengan hak pengguna (tanpa sudo)."""

    def _params(self, jenis, kw):
        def angka(nama):
            try:
                return int(kw.get(nama) or 0) or False
            except (TypeError, ValueError):
                return False
        return {'jenis': jenis, 'mitra': angka('mitra'), 'dari': angka('dari'), 'sampai': angka('sampai'),
                'anggota': angka('anggota'), 'pinjaman': angka('pinjaman'), 'shu': angka('shu')}

    def _laporan(self, p):
        laporan = request.env['koperasi.laporan'].get_laporan(
            p['jenis'], p['mitra'], p['dari'], p['sampai'], p['anggota'], p['pinjaman'], p['shu'])
        if p['jenis'] in ('kartu', 'shu') and laporan['perlu_anggota']:
            # dokumen tidak ada atau bukan hak pengguna
            raise request.not_found()
        return laporan

    @http.route('/koperasi/laporan/xlsx', type='http', auth='user')
    def laporan_xlsx(self, jenis='ringkasan', **kw):
        p = self._params(jenis, kw)
        try:
            laporan = self._laporan(p)
        except AccessError:
            raise request.not_found()
        Laporan = request.env['koperasi.laporan']
        isi = Laporan.buat_xlsx(laporan)
        return request.make_response(isi, headers=[
            ('Content-Type', XLSX_MIME),
            ('Content-Disposition', content_disposition(Laporan.nama_file(laporan, 'xlsx'))),
            ('Content-Length', len(isi)),
        ])

    @http.route('/koperasi/laporan/pdf', type='http', auth='user')
    def laporan_pdf(self, jenis='ringkasan', **kw):
        p = self._params(jenis, kw)
        try:
            laporan = self._laporan(p)  # juga memastikan hak akses sebelum PDF dibuat
            isi, _tipe = request.env['ir.actions.report']._render_qweb_pdf(
                'koperasi.laporan_pdf', data={'params': p})
        except AccessError:
            raise request.not_found()
        except UserError as e:
            return request.make_response(str(e), headers=[('Content-Type', 'text/plain; charset=utf-8')], status=500)
        nama = request.env['koperasi.laporan'].nama_file(laporan, 'pdf')
        # inline: dibuka di tab baru; pengguna tetap bisa menyimpannya dari penampil PDF
        return request.make_response(isi, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', content_disposition(nama, 'inline')),
            ('Content-Length', len(isi)),
        ])
