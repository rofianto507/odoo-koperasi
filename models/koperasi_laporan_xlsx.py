"""Penampil Excel untuk struktur laporan dari koperasi.laporan (angka asli + rumus SUM)."""
import io
from datetime import datetime

from odoo import api, models

HIJAU = '#2E7D5B'
HIJAU_MUDA = '#E8F3EC'
ABU = '#F3F4F6'


def _warna_status(teks):
    """Warna lencana status dari teks label (Belum Dibayar dicek lebih dulu)."""
    t = (teks or '').lower()
    if t.startswith('belum') or t in ('ditolak',):
        return '#FEE2E2', '#991B1B'
    if t in ('dibayar', 'lunas', 'selesai'):
        return '#D1FAE5', '#065F46'
    if t in ('berjalan', 'ditagihkan'):
        return '#DBEAFE', '#1D4ED8'
    return ABU, '#4B5563'


class KoperasiLaporanXlsx(models.AbstractModel):
    _inherit = 'koperasi.laporan'

    @api.model
    def nama_file(self, laporan, ekstensi):
        aman = ''.join(c if c.isalnum() or c in ' -_' else '-' for c in
                       '%s %s' % (laporan['judul'], laporan['periode_label']))
        return '%s.%s' % (' '.join(aman.split()), ekstensi)

    @api.model
    def buat_xlsx(self, laporan):
        import xlsxwriter  # noqa: PLC0415
        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {'in_memory': True})
        ws = wb.add_worksheet((laporan['judul'] or 'Laporan')[:31].replace('&', 'dan'))
        dasar = {'font_name': 'Calibri', 'font_size': 11, 'valign': 'vcenter'}
        f = lambda **kw: wb.add_format(dict(dasar, **kw))
        judul = f(bold=True, font_size=16)
        sub = f(font_color='#4B5563')
        label = f(bold=True)
        teks = f(border=1)
        kode = f(border=1, font_name='Consolas')
        tengah = f(border=1, align='center')
        th = f(bold=True, bg_color=HIJAU, font_color='#FFFFFF', border=1, align='center', text_wrap=True)
        judul_bagian = f(bold=True, font_size=12, font_color=HIJAU)
        catatan = f(italic=True, font_color='#666666', text_wrap=True, valign='top')
        tanggal = f(border=1, align='center', num_format='dd/mm/yyyy')
        num = lambda fmt, **kw: f(border=1, num_format=fmt, **kw)

        # lebar kolom: ambil yang terpanjang per indeks di semua bagian
        lebar = defaultdict_int()
        for b in laporan['bagian']:
            for i, k in enumerate(b['kolom']):
                isi = [len(str(r.get(k['key']) or '')) for r in b['baris'][:60]]
                lebar[i] = max(lebar[i], len(k['label']) * 0.9 + 2, (max(isi) if isi else 0) + 3)
        for i in range(max(lebar) + 1 if lebar else 1):
            ws.set_column(i, i, min(max(lebar[i], 12), 42))

        r = 0
        ws.write(r, 0, laporan.get('perusahaan') or '', sub)
        r += 1
        ws.write(r, 0, laporan['judul'].upper(), judul)
        r += 1
        ws.write(r, 0, laporan.get('subjudul') or '', sub)
        r += 2
        meta = [('Mitra', laporan['mitra_label']), ('Periode', laporan['periode_label']),
                ('Dibuat', '%s oleh %s' % (laporan['dibuat'], laporan['oleh']))]
        for k, v in laporan.get('identitas') or []:
            meta.insert(0, (k, v))
        for k, v in meta:
            ws.write(r, 0, k, label)
            ws.write(r, 1, ':', sub)
            ws.write(r, 2, v)
            r += 1
        r += 1

        if laporan.get('ringkasan'):
            ws.write(r, 0, 'RINGKASAN', judul_bagian)
            r += 1
            for item in laporan['ringkasan']:
                ws.write(r, 0, item['label'], teks)
                tipe, nilai = item['tipe'], item['nilai']
                if nilai is None:
                    ws.write(r, 1, '', teks)
                elif tipe == 'rp':
                    ws.write_number(r, 1, nilai, num('#,##0'))
                elif tipe == 'persen':
                    ws.write_number(r, 1, nilai / 100, num('0.0%'))
                else:
                    ws.write_number(r, 1, nilai, num('#,##0'))
                ws.write(r, 2, item.get('sub') or '', f(font_color='#6B7280'))
                r += 1
            r += 1

        for b in laporan['bagian']:
            ws.write(r, 0, b['judul'].upper(), judul_bagian)
            r += 1
            kolom = b['kolom']
            ws.set_row(r, 30)
            for i, k in enumerate(kolom):
                ws.write(r, i, k['label'], th)
            r += 1
            awal = r
            if not b['baris']:
                ws.merge_range(r, 0, r, len(kolom) - 1, b['kosong'], f(border=1, italic=True, font_color='#9CA3AF', align='center'))
                r += 1
            for row in b['baris']:
                gaya = row.get('_gaya')
                for i, k in enumerate(kolom):
                    v = row.get(k['key'])
                    fmt_baris = {'bold': True} if gaya in ('total', 'judul') else {}
                    if gaya == 'judul':
                        fmt_baris['bg_color'] = ABU
                    elif gaya == 'total':
                        fmt_baris['bg_color'] = HIJAU_MUDA
                    self._tulis_sel(ws, wb, r, i, v, k['tipe'], fmt_baris, dasar)
                r += 1
            if b['total'] and b['baris']:
                akhir = r - 1
                for i, k in enumerate(kolom):
                    fmt_total = f(border=1, bold=True, bg_color=HIJAU_MUDA,
                                  num_format='#,##0' if k['tipe'] in ('rp', 'angka') else 'General')
                    if i == 0:
                        ws.write(r, i, b['total'].get(k['key']), fmt_total)
                    elif k['jumlah']:
                        huruf = xlsxwriter.utility.xl_col_to_name(i)
                        ws.write_formula(r, i, '=SUM(%s%d:%s%d)' % (huruf, awal + 1, huruf, akhir + 1),
                                         fmt_total, b['total'].get(k['key']) or 0)
                    else:
                        ws.write_blank(r, i, None, fmt_total)
                r += 1
            if b.get('catatan'):
                ws.merge_range(r, 0, r, max(len(kolom) - 1, 3), b['catatan'], catatan)
                ws.set_row(r, 30)
                r += 1
            r += 1

        for c in laporan.get('catatan') or []:
            ws.merge_range(r, 0, r, 7, c, catatan)
            ws.set_row(r, 30)
            r += 1
        ws.set_landscape()
        ws.set_paper(9)
        ws.fit_to_pages(1, 0)
        ws.set_margins(left=0.4, right=0.4, top=0.6, bottom=0.6)
        ws.set_footer('&L%s&RHalaman &P dari &N' % laporan['judul'].replace('&', '&&'))
        wb.close()
        return output.getvalue()

    @api.model
    def _tulis_sel(self, ws, wb, r, c, v, tipe, ekstra, dasar):
        def fm(**kw):
            return wb.add_format(dict(dasar, border=1, **dict(ekstra, **kw)))
        if v is None or v == '':
            ws.write_blank(r, c, None, fm())
        elif tipe in ('rp', 'angka'):
            ws.write_number(r, c, v, fm(num_format='#,##0'))
        elif tipe == 'persen':
            ws.write_number(r, c, v / 100, fm(num_format='0.0%'))
        elif tipe == 'tanggal':
            try:
                ws.write_datetime(r, c, datetime.strptime(v, '%Y-%m-%d'), fm(align='center', num_format='dd/mm/yyyy'))
            except ValueError:
                ws.write(r, c, v, fm())
        elif tipe == 'status':
            bg, fg = _warna_status(v)
            ws.write(r, c, v, fm(align='center', bg_color=bg, font_color=fg))
        elif tipe == 'kode':
            ws.write(r, c, v, fm(font_name='Consolas'))
        else:
            ws.write(r, c, v, fm())


class defaultdict_int(dict):
    """dict yang mengembalikan 0 untuk kunci yang belum ada (lebar kolom)."""
    def __missing__(self, key):
        return 0
