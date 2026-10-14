import io

from odoo import models
from odoo.tools import format_date

from .koperasi_tagihan import STATUS_BARIS, STATE_TAGIHAN

KOLOM = [
    # (judul, lebar)
    ('No', 5),
    ('No. Anggota', 17),
    ('NIP / ID Karyawan', 18),
    ('Nama Anggota', 32),
    ('Jabatan', 22),
    ('Angsuran Pokok', 16),
    ('Angsuran Jasa', 15),
    ('Tunggakan', 15),
    ('Jumlah Tagihan', 17),
    ('Status Bayar', 17),
    ('Tanggal Dibayar', 16),
]
# indeks kolom (0-based) untuk rumus
K_ANG_POKOK, K_ANG_JASA, K_TUNGGAKAN = 5, 6, 7
K_JUMLAH, K_STATUS, K_TANGGAL = 8, 9, 10


class KoperasiTagihanXlsx(models.Model):
    _inherit = 'koperasi.tagihan'

    def _nama_file_xlsx(self):
        self.ensure_one()
        return 'Tagihan %s.xlsx' % self.name.replace('/', '-')

    def _buat_xlsx(self):
        """Rincian tagihan sebagai berkas Excel (bytes).

        Angka disimpan sebagai angka asli dan jumlah/total memakai rumus,
        supaya mitra bisa menyaring, menjumlah, atau mengolahnya lagi.
        Dibaca dengan hak pengguna yang memanggil, jadi aturan akses mitra
        tetap berlaku.
        """
        import xlsxwriter  # noqa: PLC0415
        self.ensure_one()
        self.check_access('read')
        mitra, periode = self.mitra_id, self.periode_id
        baris = self.line_ids.sorted(lambda l: (l.anggota_id.name or '').lower())
        status = dict(STATUS_BARIS)
        state = dict(STATE_TAGIHAN)

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet('Tagihan')

        pecahan = any(
            abs(v - round(v)) > 1e-9
            for l in baris for v in (l.angsuran_pokok, l.angsuran_jasa, l.tunggakan))
        fmt_angka = '#,##0.00' if pecahan else '#,##0'
        dasar = {'font_name': 'Calibri', 'font_size': 11, 'valign': 'vcenter'}
        f = lambda **kw: wb.add_format(dict(dasar, **kw))
        judul = f(bold=True, font_size=16)
        label = f(bold=True)
        info = f(align='left')
        th = f(bold=True, bg_color='#2E7D5B', font_color='#FFFFFF', border=1,
               align='center', text_wrap=True)
        teks = f(border=1)
        tengah = f(border=1, align='center')
        angka = f(border=1, num_format=fmt_angka)
        angka_tebal = f(border=1, bold=True, num_format=fmt_angka, bg_color='#E8F3EC')
        tebal_total = f(border=1, bold=True, bg_color='#E8F3EC')
        tanggal = f(border=1, align='center', num_format='dd/mm/yyyy')
        catatan = f(italic=True, font_color='#666666')

        for i, (_judul, lebar) in enumerate(KOLOM):
            ws.set_column(i, i, lebar)

        ws.write(0, 0, 'TAGIHAN KOPERASI', judul)
        meta = [
            ('Mitra', '%s (%s)' % (mitra.name, mitra.kode)),
            ('Nomor Tagihan', self.name),
            ('Periode', periode.name),
            ('Status Tagihan', state.get(self.state, self.state)),
            ('Tanggal Ditagihkan',
             format_date(self.env, self.tanggal_terbit) if self.tanggal_terbit else '-'),
        ]
        for i, (k, v) in enumerate(meta, start=2):
            ws.write(i, 0, k, label)
            ws.write(i, 2, ':', info)
            ws.write(i, 3, v, info)

        header = len(meta) + 3       # baris judul kolom (0-based)
        awal = header + 1            # baris data pertama
        akhir = awal + len(baris) - 1
        ws.set_row(header, 32)
        for i, (judul_kolom, _lebar) in enumerate(KOLOM):
            ws.write(header, i, judul_kolom, th)

        def sel(baris_, kolom):  # alamat sel gaya Excel, mis. F8
            return xlsxwriter.utility.xl_rowcol_to_cell(baris_, kolom)

        for n, l in enumerate(baris):
            r = awal + n
            a = l.anggota_id
            ws.write_number(r, 0, n + 1, tengah)
            ws.write(r, 1, a.nomor_anggota or '', teks)
            ws.write(r, 2, a.nip or '', teks)
            ws.write(r, 3, a.name or '', teks)
            ws.write(r, 4, a.jabatan or '', teks)
            ws.write_number(r, K_ANG_POKOK, l.angsuran_pokok, angka)
            ws.write_number(r, K_ANG_JASA, l.angsuran_jasa, angka)
            ws.write_number(r, K_TUNGGAKAN, l.tunggakan, angka)
            ws.write_formula(
                r, K_JUMLAH,
                '=SUM(%s:%s)' % (sel(r, K_ANG_POKOK), sel(r, K_TUNGGAKAN)), angka, l.total)
            ws.write(r, K_STATUS, status.get(l.status, l.status), tengah)
            if l.tanggal_bayar:
                ws.write_datetime(r, K_TANGGAL, l.tanggal_bayar, tanggal)
            else:
                ws.write_blank(r, K_TANGGAL, None, tanggal)

        total = akhir + 1
        ws.merge_range(total, 0, total, 4, 'TOTAL', tebal_total)
        nilai_total = {
            K_ANG_POKOK: self.total_angsuran_pokok,
            K_ANG_JASA: self.total_angsuran_jasa, K_TUNGGAKAN: self.total_tunggakan,
            K_JUMLAH: self.total,
        }
        for k, nilai in nilai_total.items():
            if baris:
                ws.write_formula(
                    total, k, '=SUM(%s:%s)' % (sel(awal, k), sel(akhir, k)), angka_tebal, nilai)
            else:
                ws.write_number(total, k, 0, angka_tebal)
        ws.write_blank(total, K_STATUS, None, tebal_total)
        ws.write_blank(total, K_TANGGAL, None, tebal_total)

        # Ringkasan pembayaran (rumus SUMIF, ikut berubah bila mitra mengedit status)
        ringkas = total + 2
        ws.write(ringkas, 3, 'Ringkasan Pembayaran', label)
        rentang_status = '%s:%s' % (sel(awal, K_STATUS), sel(akhir, K_STATUS))
        rentang_jumlah = '%s:%s' % (sel(awal, K_JUMLAH), sel(akhir, K_JUMLAH))
        for i, (ket, kunci, nilai) in enumerate((
            ('Sudah Dibayar', status['dibayar'], self.total_dibayar),
            ('Belum Dibayar', status['belum'], self.total_belum),
        ), start=1):
            ws.write(ringkas + i, 3, ket, teks)
            if baris:
                ws.write_formula(
                    ringkas + i, 4,
                    '=SUMIF(%s,"%s",%s)' % (rentang_status, kunci, rentang_jumlah),
                    angka, nilai)
            else:
                ws.write_number(ringkas + i, 4, 0, angka)
        ws.write(ringkas + 4, 0,
                 'Belum Dibayar = ditagihkan kembali pada periode berikutnya sebagai tunggakan.',
                 catatan)

        if baris:
            ws.autofilter(header, 0, akhir, len(KOLOM) - 1)
        ws.freeze_panes(awal, 4)
        # Siap cetak: A4 landscape, lebar pas satu halaman, judul kolom diulang
        ws.set_landscape()
        ws.set_paper(9)
        ws.fit_to_pages(1, 0)
        ws.repeat_rows(header)
        ws.set_margins(left=0.4, right=0.4, top=0.6, bottom=0.6)
        ws.set_footer('&LTagihan %s&RHalaman &P dari &N' % self.name.replace('/', '-').replace('&', '&&'))
        wb.close()
        return output.getvalue()

    def action_unduh_excel(self):
        self.ensure_one()
        self.check_access('read')
        return {
            'type': 'ir.actions.act_url',
            'url': '/koperasi/tagihan/%d/xlsx' % self.id,
            'target': 'self',
        }
