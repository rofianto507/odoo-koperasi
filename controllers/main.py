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

    def _params(self, jenis, mitra, dari, sampai, anggota):
        return {'jenis': jenis, 'mitra': mitra or False, 'dari': dari or False,
                'sampai': sampai or False, 'anggota': anggota or False}

    def _laporan(self, p):
        return request.env['koperasi.laporan'].get_laporan(
            p['jenis'], p['mitra'], p['dari'], p['sampai'], p['anggota'])

    @http.route('/koperasi/laporan/xlsx', type='http', auth='user')
    def laporan_xlsx(self, jenis='ringkasan', mitra=0, dari=0, sampai=0, anggota=0, **kw):
        p = self._params(jenis, int(mitra or 0), int(dari or 0), int(sampai or 0), int(anggota or 0))
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
    def laporan_pdf(self, jenis='ringkasan', mitra=0, dari=0, sampai=0, anggota=0, **kw):
        p = self._params(jenis, int(mitra or 0), int(dari or 0), int(sampai or 0), int(anggota or 0))
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
