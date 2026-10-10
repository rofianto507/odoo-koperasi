"""Akses portal anggota: undangan (tautan/QR), pembuatan akun, dan data yang ditampilkan portal.

Akun anggota adalah pengguna portal Odoo (base.group_portal) yang ditautkan ke satu anggota.
Pengguna portal tidak diberi hak akses model apa pun: semua data dibaca lewat controller
portal dengan sudo, dan selalu dibatasi ke anggota milik pengguna yang sedang login.
"""
import base64
import re
import secrets
from datetime import timedelta
from urllib.parse import quote

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.fields import Command
from odoo.http import db_list, request

MASA_UNDANGAN_HARI = 7
POLA_USERNAME = re.compile(r'^[a-z0-9][a-z0-9._-]{3,31}$')
PANJANG_PASSWORD_MIN = 8
STATUS_PORTAL = [
    ('belum', 'Belum Ada Akun'),
    ('diundang', 'Undangan Aktif'),
    ('aktif', 'Akun Aktif'),
    ('nonaktif', 'Akun Nonaktif'),
]


class ResUsers(models.Model):
    _inherit = 'res.users'

    # Pengguna portal dengan anggota terisi = akun anggota tsb. Tidak masuk
    # SELF_WRITEABLE_FIELDS, jadi tidak bisa diubah oleh penggunanya sendiri.
    koperasi_anggota_id = fields.Many2one(
        'koperasi.anggota', string='Anggota Koperasi', index=True, ondelete='set null', copy=False)

    _koperasi_anggota_uniq = models.Constraint(
        'UNIQUE(koperasi_anggota_id)', 'Anggota tersebut sudah memiliki akun portal.')


class KoperasiAnggota(models.Model):
    _inherit = 'koperasi.anggota'

    user_id = fields.Many2one(
        'res.users', string='Akun Portal', readonly=True, copy=False, ondelete='set null')
    # token undangan tidak ikut terbaca lewat RPC; tautannya dihitung dengan sudo
    undangan_token = fields.Char(copy=False, index=True, groups='base.group_system')
    undangan_berlaku = fields.Datetime('Undangan Berlaku Sampai', readonly=True, copy=False)
    undangan_url = fields.Char('Tautan Undangan', compute='_compute_undangan')
    undangan_qr = fields.Binary('Kode QR', compute='_compute_undangan')
    portal_url = fields.Char('Alamat Portal', compute='_compute_undangan')
    portal_status = fields.Selection(STATUS_PORTAL, string='Akses Portal', compute='_compute_portal')
    portal_login = fields.Char('Username Portal', compute='_compute_portal')
    portal_login_terakhir = fields.Datetime('Login Terakhir', compute='_compute_portal')

    # ------------------------------------------------------------------
    # Status dan tautan
    # ------------------------------------------------------------------
    def _undangan_berlaku(self):
        self.ensure_one()
        r = self.sudo()
        return bool(r.undangan_token and r.undangan_berlaku and r.undangan_berlaku > fields.Datetime.now())

    @api.depends('user_id', 'undangan_berlaku')
    def _compute_portal(self):
        for r in self:
            user = r.sudo().with_context(active_test=False).user_id
            r.portal_login = user.login
            r.portal_login_terakhir = user.login_date
            if user:
                r.portal_status = 'aktif' if user.active else 'nonaktif'
            else:
                r.portal_status = 'diundang' if r._undangan_berlaku() else 'belum'

    def _alamat_dasar(self):
        """Alamat yang sedang dipakai pembuka halaman (agar tautan bisa dibuka dari jaringan yang
        sama), atau web.base.url bila dihitung di luar permintaan web."""
        if request:
            return request.httprequest.host_url.rstrip('/')
        return self.get_base_url()

    @api.model
    def _tautan_publik(self, jalur):
        """Alamat lengkap sebuah halaman portal untuk dibagikan ke anggota.

        Bila server melayani lebih dari satu database, pengunjung tanpa sesi tidak bisa
        langsung membuka rute modul (Odoo belum tahu databasenya). Untuk keadaan itu
        tautan dilewatkan pintu masuk bawaan Odoo yang menetapkan database lebih dulu:
        /web/login?db=<nama>&redirect=<jalur> (lihat KoperasiHome di controllers/portal.py)."""
        dasar = self._alamat_dasar()
        try:
            banyak_db = len(db_list(force=True, host=request.httprequest.environ['HTTP_HOST'] if request else None)) > 1
        except Exception:
            banyak_db = False
        if banyak_db:
            return '%s/web/login?db=%s&redirect=%s' % (dasar, quote(self.env.cr.dbname), quote(jalur, safe=''))
        return dasar + jalur

    @api.depends('undangan_berlaku')
    def _compute_undangan(self):
        Laporan = self.env['ir.actions.report']
        portal = self._tautan_publik('/koperasi/portal')
        for r in self:
            r.portal_url = portal
            if not r._undangan_berlaku():
                r.undangan_url = r.undangan_qr = False
                continue
            r.undangan_url = self._tautan_publik('/koperasi/daftar/%s' % r.sudo().undangan_token)
            r.undangan_qr = base64.b64encode(Laporan.barcode(
                'QR', r.undangan_url, width=320, height=320, barLevel='M'))

    # ------------------------------------------------------------------
    # Undangan (operator atau admin mitra anggota tersebut)
    # ------------------------------------------------------------------
    def _cek_pengelola_portal(self):
        """Record rule sudah membatasi admin mitra ke anggota mitranya; di sini cukup dipastikan
        pengguna memang pengelola (bukan pengguna portal) dan boleh membaca anggota ini."""
        if not self.env.user.has_group('koperasi.group_koperasi_mitra'):
            raise UserError(_('Hanya operator pusat atau admin mitra yang dapat mengatur akses portal.'))
        self.check_access('read')

    def _aksi_portal(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Akses Portal - %s', self.name),
            'res_model': 'koperasi.anggota',
            'res_id': self.id,
            'view_mode': 'form',
            'views': [(self.env.ref('koperasi.view_koperasi_anggota_portal_form').id, 'form')],
            'target': 'new',
        }

    def action_portal(self):
        self.ensure_one()
        self._cek_pengelola_portal()
        return self._aksi_portal()

    def action_buat_undangan(self):
        """Buat tautan baru (yang lama langsung tidak berlaku). Bila anggota sudah punya akun,
        tautan ini dipakai untuk mengatur ulang password."""
        self.ensure_one()
        self._cek_pengelola_portal()
        if self.state != 'aktif' and not self.sudo().user_id:
            raise UserError(_('Akun portal hanya dapat dibuat untuk anggota berstatus Aktif.'))
        # sudo: admin mitra tidak boleh menulis data anggota aktif secara langsung (lihat write())
        self.sudo().write({
            'undangan_token': secrets.token_urlsafe(32),
            'undangan_berlaku': fields.Datetime.now() + timedelta(days=MASA_UNDANGAN_HARI),
        })
        self.message_post(body=_(
            'Tautan %(jenis)s portal dibuat, berlaku %(hari)d hari.',
            jenis=_('atur ulang password') if self.sudo().user_id else _('undangan'),
            hari=MASA_UNDANGAN_HARI))
        return self._aksi_portal()

    def action_cabut_undangan(self):
        self.ensure_one()
        self._cek_pengelola_portal()
        self.sudo().write({'undangan_token': False, 'undangan_berlaku': False})
        return self._aksi_portal()

    def action_bagikan_whatsapp(self):
        self.ensure_one()
        self._cek_pengelola_portal()
        if not self.undangan_url:
            raise UserError(_('Buat tautan undangan dulu.'))
        nomor = re.sub(r'\D', '', self.telepon or '')
        if nomor.startswith('0'):
            nomor = '62' + nomor[1:]
        pesan = _(
            'Halo %(nama)s, berikut tautan untuk %(tujuan)s portal anggota koperasi. '
            'Berlaku sampai %(sampai)s.\n%(url)s',
            nama=self.name,
            tujuan=_('mengatur ulang password') if self.sudo().user_id else _('membuat akun'),
            sampai=fields.Datetime.context_timestamp(self, self.undangan_berlaku).strftime('%d/%m/%Y %H:%M'),
            url=self.undangan_url)
        return {
            'type': 'ir.actions.act_url',
            'url': 'https://wa.me/%s?text=%s' % (nomor, quote(pesan)),
            'target': 'new',
        }

    def action_nonaktifkan_portal(self):
        """Operator: matikan atau hidupkan kembali akun portal anggota."""
        self.ensure_one()
        if not self.env.user.has_group('koperasi.group_koperasi_operator'):
            raise UserError(_('Hanya operator pusat yang dapat menonaktifkan akun portal.'))
        user = self.sudo().with_context(active_test=False).user_id
        if user:
            user.active = not user.active
            self.message_post(body=_('Akun portal %s.', _('diaktifkan kembali') if user.active else _('dinonaktifkan')))
        return self._aksi_portal()

    # ------------------------------------------------------------------
    # Dipanggil controller portal (pengguna publik) -> selalu sudo di sini
    # ------------------------------------------------------------------
    @api.model
    def _dari_token(self, token):
        """Anggota pemilik token yang masih berlaku, atau recordset kosong."""
        if not token or len(token) < 20:
            return self.sudo().browse()
        anggota = self.sudo().search([('undangan_token', '=', token)], limit=1)
        return anggota if anggota and anggota._undangan_berlaku() else self.sudo().browse()

    @api.model
    def _cek_username(self, username):
        username = (username or '').strip().lower()
        if not POLA_USERNAME.match(username):
            raise UserError(_(
                'Username 4-32 karakter: huruf kecil, angka, titik, garis bawah, atau tanda hubung, '
                'dan diawali huruf atau angka.'))
        if self.env['res.users'].sudo().with_context(active_test=False).search_count(
                [('login', '=ilike', username)], limit=1):
            raise UserError(_('Username "%s" sudah dipakai. Pilih yang lain.', username))
        return username

    @api.model
    def _cek_password(self, password):
        if not password or len(password) < PANJANG_PASSWORD_MIN:
            raise UserError(_('Password minimal %d karakter.', PANJANG_PASSWORD_MIN))
        if not (re.search(r'[A-Za-z]', password) and re.search(r'[0-9]', password)):
            raise UserError(_('Password harus memuat huruf dan angka.'))
        return password

    def _daftar_portal(self, username, password, foto=None):
        """Selesaikan undangan: buat akun portal (atau atur ulang password akun yang ada).
        Mengembalikan login yang dipakai. Token hangus setelah berhasil."""
        self.ensure_one()
        anggota = self.sudo()
        self._cek_password(password)
        user = anggota.with_context(active_test=False).user_id
        baru = not user
        if user:
            user.with_context(no_reset_password=True).write({'password': password, 'active': True})
        else:
            login = self._cek_username(username)
            user = self.env['res.users'].sudo().with_context(no_reset_password=True).create({
                # kontak anggota sudah ada (koperasi.kontak.mixin); nama dipakai bila belum
                'partner_id': anggota.partner_id.id,
                'name': anggota.name,
                'login': login,
                'password': password,
                'group_ids': [Command.set(self.env.ref('base.group_portal').ids)],
                'koperasi_anggota_id': anggota.id,
            })
            anggota.user_id = user
        nilai = {'undangan_token': False, 'undangan_berlaku': False}
        if foto:
            nilai['image_1920'] = foto
        anggota.write(nilai)
        anggota.message_post(
            author_id=user.partner_id.id,
            body=_('Akun portal dibuat lewat tautan undangan.') if baru
            else _('Password akun portal diatur ulang lewat tautan.'))
        return user.login

    # ------------------------------------------------------------------
    # Data untuk halaman portal (dipanggil dengan sudo oleh controller, selalu untuk SATU anggota)
    # ------------------------------------------------------------------
    def _portal_identitas(self):
        self.ensure_one()
        label = dict(self._fields['state'].selection)
        return {
            'nama': self.name, 'nomor': self.nomor_anggota or '', 'mitra': self.mitra_id.name,
            'status': self.state, 'status_label': label.get(self.state, ''),
            # penanda versi supaya foto baru tidak tertahan cache peramban
            'foto': '/koperasi/portal/foto?v=%d' % int((self.write_date or fields.Datetime.now()).timestamp()),
        }

    def _portal_saldo(self):
        self.ensure_one()
        return {'pokok': self.saldo_simpok, 'wajib': self.saldo_simwa, 'sukarela': self.saldo_simsula,
                'total': self.saldo_total}

    def _portal_beranda(self):
        self.ensure_one()
        Line = self.env['koperasi.tagihan.line']
        label_baris = dict(Line._fields['status'].selection)
        berjalan = self.pinjaman_ids.filtered(lambda p: p.state == 'berjalan')[:1]
        pengajuan = self.pinjaman_ids.filtered(lambda p: p.state in ('diajukan', 'disetujui'))
        ditagihkan = Line.search([('anggota_id', '=', self.id), ('tagihan_id.state', '!=', 'draft')])
        terakhir = ditagihkan.sorted(lambda l: (l.periode, l.id), reverse=True)[:1]
        aktivitas = []
        for l in ditagihkan.filtered(lambda l: l.status == 'dibayar'):
            simpanan = l.simpok + l.simwa + l.simsula
            angsuran = l.angsuran_pokok + l.angsuran_jasa
            aktivitas.append({
                'tanggal': l.tanggal_bayar or l.periode, 'ikon': 'fa-check-circle', 'warna': 'hijau',
                'judul': _('Tagihan %s dibayar', l.periode_id.name),
                'sub': _('simpanan %s', self._rp(simpanan)) + (
                    _(' · angsuran %s', self._rp(angsuran)) if angsuran else ''),
                'nilai': simpanan + angsuran})
        for t in self.penarikan_ids.filtered(lambda t: t.state == 'dibayar'):
            aktivitas.append({
                'tanggal': t.tanggal_bayar, 'ikon': 'fa-sign-out', 'warna': 'jingga',
                'judul': _('Penarikan simpanan'), 'sub': t.name, 'nilai': -t.total})
        for p in self.pinjaman_ids.filtered('tanggal_cair'):
            aktivitas.append({
                'tanggal': p.tanggal_cair, 'ikon': 'fa-credit-card', 'warna': 'biru',
                'judul': _('Pinjaman dicairkan'), 'sub': p.name, 'nilai': p.diterima})
        aktivitas.sort(key=lambda x: x['tanggal'], reverse=True)
        for x in aktivitas:
            x['tanggal'] = fields.Date.to_string(x['tanggal'])
        return {
            'anggota': self._portal_identitas(),
            'saldo': self._portal_saldo(),
            'tunggakan': self.tunggakan_total,
            'pinjaman': {
                'id': berjalan.id, 'nomor': berjalan.name, 'pokok': berjalan.pokok, 'sisa': berjalan.sisa_pokok,
                'dibayar': berjalan.angsuran_dibayar, 'tenor': berjalan.tenor, 'angsuran': berjalan.angsuran_bulan,
                'persen': round(berjalan.angsuran_dibayar * 100 / berjalan.tenor) if berjalan.tenor else 0,
            } if berjalan else False,
            'pengajuan': len(pengajuan),
            'tagihan': {
                'periode': terakhir.periode_id.name, 'total': terakhir.total,
                'simpanan': terakhir.simpok + terakhir.simwa + terakhir.simsula,
                'angsuran': terakhir.angsuran_pokok + terakhir.angsuran_jasa, 'tunggakan': terakhir.tunggakan,
                'status': terakhir.status, 'status_label': label_baris[terakhir.status],
            } if terakhir else False,
            'aktivitas': aktivitas[:6],
        }

    def _portal_simpanan(self):
        self.ensure_one()
        cfg = self.env['koperasi.pengaturan']._get()
        lap = self.env['koperasi.laporan'].get_laporan('anggota', anggota_id=self.id)
        mutasi = [{
            'periode': r['periode'], 'pokok': r['simpok'] or 0, 'wajib': r['simwa'] or 0,
            'sukarela': r['simsula'] or 0, 'mutasi': r['diterima'] or 0, 'saldo': r['saldo'], 'status': r['status'],
        } for r in (lap['bagian'][0]['baris'] if lap.get('bagian') else []) if not r.get('_gaya')]
        Penarikan = self.env['koperasi.penarikan']
        label = dict(Penarikan._fields['state'].selection)
        jenis = dict(Penarikan._fields['jenis'].selection)
        aktif = self.state == 'aktif'
        return {
            'saldo': self._portal_saldo(),
            'bulanan': {'wajib': cfg.simwa if aktif else 0, 'sukarela': self.simsula if aktif else 0},
            'mutasi': mutasi[::-1],
            'penarikan': [{
                'nomor': t.name, 'jenis': jenis[t.jenis], 'total': t.total, 'status': t.state,
                'status_label': label[t.state],
                'tanggal': fields.Date.to_string(
                    t.tanggal_bayar or (t.tanggal_ajukan and t.tanggal_ajukan.date()) or t.create_date.date()),
            } for t in self.penarikan_ids.filtered(lambda t: t.state != 'draft').sorted('id', reverse=True)],
        }

    def _portal_pinjaman(self):
        self.ensure_one()
        Pinjaman = self.env['koperasi.pinjaman']
        label = dict(Pinjaman._fields['state'].selection)
        label_kualitas = dict(Pinjaman._fields['kualitas'].selection)
        label_angsuran = dict(self.env['koperasi.pinjaman.angsuran']._fields['status'].selection)
        daftar = []
        for p in self.pinjaman_ids.filtered(lambda p: p.state != 'draft').sorted('id', reverse=True):
            jadwal = []
            for x in p.angsuran_ids.sorted('ke'):
                belum = x.tagihan_line_id.status == 'belum'
                jadwal.append({
                    'ke': x.ke, 'periode': x.periode_id.name, 'pokok': x.pokok, 'jasa': x.jasa, 'total': x.total,
                    'status': 'belum' if belum else x.status,
                    'status_label': _('Belum Dibayar') if belum else label_angsuran[x.status]})
            daftar.append({
                'id': p.id, 'nomor': p.name, 'produk': p.produk_id.name, 'keperluan': p.keperluan or '',
                'pokok': p.pokok, 'tenor': p.tenor, 'angsuran': p.angsuran_bulan, 'jasa_bulan': p.jasa_bulan,
                'total_jasa': p.total_jasa, 'biaya_admin': p.biaya_admin, 'diterima': p.diterima,
                'sisa': p.sisa_pokok if p.state in ('berjalan', 'lunas') else p.pokok,
                'dibayar': p.angsuran_dibayar,
                'persen': round(p.angsuran_dibayar * 100 / p.tenor) if p.tenor else 0,
                'status': p.state, 'status_label': label[p.state],
                'kualitas': p.kualitas or '', 'kualitas_label': label_kualitas.get(p.kualitas, ''),
                'tanggal_cair': fields.Date.to_string(p.tanggal_cair) if p.tanggal_cair else '',
                'alasan_tolak': (p.alasan_tolak or '') if p.state == 'ditolak' else '',
                'jadwal': jadwal,
            })
        return {'daftar': daftar}

    def _portal_profil(self):
        self.ensure_one()
        tgl = lambda d: d.strftime('%d/%m/%Y') if d else ''
        return {
            'anggota': self._portal_identitas(),
            'username': self.user_id.login,
            'pribadi': [
                (_('NIK'), self.nik or '-'),
                (_('Tempat, tanggal lahir'),
                 ', '.join(x for x in (self.tempat_lahir, tgl(self.tanggal_lahir)) if x) or '-'),
                (_('Telp / WA'), self.telepon or '-'),
                (_('Email'), self.email or '-'),
            ],
            'kepegawaian': [
                (_('Mitra'), self.mitra_id.name),
                (_('NIP / ID Karyawan'), self.nip or '-'),
                (_('Jabatan'), self.jabatan or '-'),
                (_('Bergabung'), tgl(self.tanggal_bergabung) or '-'),
            ] + ([(_('Keluar'), tgl(self.tanggal_keluar))] if self.tanggal_keluar else []),
            'rekening': [
                (_('Bank'), self._nama_bank() or '-'),
                (_('Nomor rekening'), self.nomor_rekening or '-'),
                (_('Atas nama'), self.atas_nama or '-'),
            ],
        }

    @api.model
    def _rp(self, nilai):
        return 'Rp %s' % '{:,.0f}'.format(nilai or 0).replace(',', '.')
