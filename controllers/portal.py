"""Portal anggota: pendaftaran lewat tautan undangan, login, aplikasi, dan API datanya.

Halaman-halaman ini berdiri sendiri (bukan backend Odoo): HTML ringkas yang memuat OWL bawaan
Odoo dan satu berkas JavaScript. Semua rute `auth='public'`; hak ditentukan di sini:
pengguna harus login sebagai akun portal yang tertaut ke anggota, dan setiap data diambil
dengan sudo HANYA untuk anggota milik pengguna itu (tidak pernah dari parameter permintaan).
"""
import base64
import binascii
import json
import logging
import threading
import time
from collections import defaultdict

from odoo import http
from odoo.exceptions import AccessDenied, UserError
from odoo.http import content_disposition, db_list, request
from odoo.modules.module import get_manifest

from odoo.addons.web.controllers.home import Home

_logger = logging.getLogger(__name__)

UKURAN_FOTO_MAKS = 3 * 1024 * 1024  # byte, setelah di-decode
# Pembatas sederhana per alamat IP (dalam memori, satu proses): (maks, jendela detik)
BATAS = {'daftar': (10, 3600), 'login': (20, 900), 'password': (10, 3600)}
_kunci_batas = threading.Lock()
_catatan_batas = defaultdict(list)


def _boleh(jenis):
    """Catat satu percobaan; False bila alamat ini sudah melewati batas."""
    maks, jendela = BATAS[jenis]
    kunci = '%s:%s' % (jenis, request.httprequest.remote_addr)
    sekarang = time.time()
    with _kunci_batas:
        _catatan_batas[kunci] = [t for t in _catatan_batas[kunci] if sekarang - t < jendela]
        if len(_catatan_batas[kunci]) >= maks:
            return False
        _catatan_batas[kunci].append(sekarang)
        return True


def _foto_valid(b64):
    """Kembalikan data base64 bila berupa JPEG/PNG/WebP berukuran wajar; selain itu galat."""
    if not b64:
        return None
    if ',' in b64[:64]:  # data URL
        b64 = b64.split(',', 1)[1]
    try:
        mentah = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise UserError('Berkas foto tidak dapat dibaca.')
    if len(mentah) > UKURAN_FOTO_MAKS:
        raise UserError('Ukuran foto terlalu besar.')
    if not (mentah[:3] == b'\xff\xd8\xff' or mentah[:8] == b'\x89PNG\r\n\x1a\n'
            or (mentah[:4] == b'RIFF' and mentah[8:12] == b'WEBP')):
        raise UserError('Foto harus berupa gambar JPEG, PNG, atau WebP.')
    return b64


SERVICE_WORKER = r"""/* Koperasi - Portal Anggota: service worker (dibuat oleh controllers/portal.py) */
const CACHE = %(versi)s;
const PINTU = %(pintu)s;
const KERANGKA = %(kerangka)s;
const STATIS = ['/koperasi/static/src/portal/', '/web/static/lib/owl/', '/web/static/src/libs/fontawesome/'];
const HALAMAN_PORTAL = ['/koperasi/portal', '/koperasi/portal/', '/koperasi/portal/login'];
const OFFLINE = '<!doctype html><html lang="id"><head><meta charset="utf-8">' +
    '<meta name="viewport" content="width=device-width, initial-scale=1"><title>Tidak ada koneksi</title>' +
    '<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;' +
    'font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:#f6f6f8;color:#2b2a33;text-align:center;padding:24px}' +
    'h1{font-size:19px;margin:14px 0 6px}p{color:#7a788a;font-size:14px;margin:0 0 20px}' +
    'button{border:none;border-radius:14px;padding:13px 26px;background:#71639e;color:#fff;font-weight:600;font-size:15px}' +
    '</style></head><body><div><div style="font-size:42px">&#128268;</div><h1>Tidak ada koneksi</h1>' +
    '<p>Portal anggota membutuhkan internet untuk menampilkan data terbaru.</p>' +
    '<button onclick="location.reload()">Coba Lagi</button></div></body></html>';

self.addEventListener('install', (ev) => {
    self.skipWaiting();
    ev.waitUntil(caches.open(CACHE).then((c) => c.addAll(KERANGKA)).catch(() => {}));
});

self.addEventListener('activate', (ev) => {
    ev.waitUntil(
        caches.keys()
            .then((kunci) => Promise.all(kunci.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
            .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (ev) => {
    const req = ev.request;
    if (req.method !== 'GET') return;
    const url = new URL(req.url);
    if (url.origin !== self.location.origin) return;

    if (req.mode === 'navigate') {
        ev.respondWith(
            fetch(req).then((res) => {
                // sesi habis di server multi-database: tetapkan database dulu lewat pintu masuk
                if (res.status === 404 && PINTU && HALAMAN_PORTAL.includes(url.pathname)) {
                    return Response.redirect(PINTU, 302);
                }
                return res;
            }).catch(() => new Response(OFFLINE, { status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
        return;
    }

    if (STATIS.some((awalan) => url.pathname.startsWith(awalan))) {
        // berkas statis: dari simpanan bila ada, selebihnya dari jaringan lalu disimpan
        ev.respondWith(
            caches.match(req).then((ada) => ada || fetch(req).then((res) => {
                if (res.ok) {
                    const salinan = res.clone();
                    caches.open(CACHE).then((c) => c.put(req, salinan));
                }
                return res;
            })));
    }
    // selain itu (API, foto, PDF): langsung ke jaringan, tidak disimpan
});
"""


class KoperasiHome(Home):
    """Pintu masuk tautan portal pada server dengan lebih dari satu database.

    Tautan berbentuk /web/login?db=<nama>&redirect=/koperasi/...: rute login bawaan Odoo
    menetapkan database pada sesi pengunjung, lalu di sini pengunjung langsung diteruskan ke
    halaman portal alih-alih melihat form login backend. Hanya jalur portal yang diteruskan."""

    @http.route()
    def web_login(self, redirect=None, **kw):
        if (request.httprequest.method == 'GET' and redirect and request.db
                and (redirect == '/koperasi/portal' or redirect.startswith('/koperasi/daftar/'))
                and '//' not in redirect and chr(92) not in redirect):  # chr(92) = garis miring terbalik
            return request.redirect(redirect)
        return super().web_login(redirect=redirect, **kw)


class KoperasiPortal(http.Controller):

    # ------------------------------------------------------------------
    # Bantuan
    # ------------------------------------------------------------------
    def _anggota(self):
        """Anggota milik pengguna yang login (sudo), atau None."""
        user = request.env.user
        if not user or user._is_public():
            return None
        anggota = user.sudo().koperasi_anggota_id
        return anggota or None

    def _render(self, template, nilai=None):
        nilai = dict(nilai or {})
        # penanda versi berkas statis: berubah tiap versi modul naik
        nilai.setdefault('versi', get_manifest('koperasi').get('version', '0'))
        nilai.setdefault('perusahaan', request.env.company.sudo().name)
        respons = request.render(template, nilai)
        respons.headers['Cache-Control'] = 'no-store'
        return respons

    def _api(self, fungsi):
        """Jalankan pembaca data untuk anggota yang login; bentuk jawaban seragam."""
        anggota = self._anggota()
        if not anggota:
            return {'error': 'auth'}
        try:
            return {'data': fungsi(anggota)}
        except UserError as e:
            return {'error': 'pesan', 'pesan': str(e)}

    # ------------------------------------------------------------------
    # Pendaftaran lewat tautan undangan
    # ------------------------------------------------------------------
    @http.route('/koperasi/daftar/<string:token>', type='http', auth='public', website=False)
    def daftar(self, token, **kw):
        anggota = request.env['koperasi.anggota']._dari_token(token)
        if not anggota:
            return self._render('koperasi.portal_pesan', {
                'judul': 'Tautan tidak berlaku',
                'pesan': 'Tautan undangan ini sudah dipakai, dicabut, atau kedaluwarsa. '
                         'Minta tautan baru kepada admin di tempat kerja Anda atau pengurus koperasi.',
            })
        punya_akun = bool(anggota.with_context(active_test=False).user_id)
        return self._render('koperasi.portal_daftar', {
            'init_data': json.dumps({
                'token': token,
                'nama': anggota.name,
                'nomor': anggota.nomor_anggota or '',
                'mitra': anggota.mitra_id.name,
                'atur_ulang': punya_akun,
                'username': anggota.with_context(active_test=False).user_id.login or '',
                'punya_foto': bool(anggota.image_1920),
            }),
        })

    @http.route('/koperasi/daftar/submit', type='jsonrpc', auth='public')
    def daftar_submit(self, token=None, username=None, password=None, foto=None, **kw):
        if not _boleh('daftar'):
            return {'error': 'Terlalu banyak percobaan. Coba lagi nanti.'}
        Anggota = request.env['koperasi.anggota']
        anggota = Anggota._dari_token(token)
        if not anggota:
            return {'error': 'Tautan undangan sudah tidak berlaku. Minta tautan baru.'}
        try:
            login = anggota._daftar_portal(username, password, _foto_valid(foto))
        except UserError as e:
            return {'error': str(e)}
        # langsung masuk dengan akun yang baru dibuat
        try:
            request.session.authenticate(request.env, {'type': 'password', 'login': login, 'password': password})
        except AccessDenied:
            return {'ok': True, 'redirect': '/koperasi/portal/login'}
        return {'ok': True, 'redirect': '/koperasi/portal'}

    # ------------------------------------------------------------------
    # Login / logout
    # ------------------------------------------------------------------
    @http.route('/koperasi/portal/login', type='http', auth='public', website=False, methods=['GET'])
    def login(self, error=None, **kw):
        if self._anggota():
            return request.redirect('/koperasi/portal')
        user = request.env.user
        return self._render('koperasi.portal_login', {
            'error': error,
            'csrf': request.csrf_token(),
            # sudah login dengan akun lain (mis. operator): beri tahu, jangan diam-diam diganti
            'akun_lain': False if user._is_public() else user.name,
        })

    @http.route('/koperasi/portal/login', type='http', auth='public', website=False, methods=['POST'])
    def login_post(self, login='', password='', **kw):
        if not _boleh('login'):
            return request.redirect('/koperasi/portal/login?error=batas')
        try:
            request.session.authenticate(
                request.env, {'type': 'password', 'login': login.strip().lower(), 'password': password})
        except AccessDenied:
            return request.redirect('/koperasi/portal/login?error=salah')
        user = request.env['res.users'].sudo().browse(request.session.uid)
        if not user.koperasi_anggota_id:
            request.session.logout(keep_db=True)
            return request.redirect('/koperasi/portal/login?error=bukan')
        return request.redirect('/koperasi/portal')

    @http.route('/koperasi/portal/logout', type='http', auth='public', website=False)
    def logout(self, **kw):
        if self._anggota():
            request.session.logout(keep_db=True)
        return request.redirect('/koperasi/portal/login')

    # ------------------------------------------------------------------
    # Aplikasi
    # ------------------------------------------------------------------
    @http.route(['/koperasi/portal', '/koperasi/portal/'], type='http', auth='public', website=False)
    def portal(self, **kw):
        anggota = self._anggota()
        if not anggota:
            return request.redirect('/koperasi/portal/login')
        return self._render('koperasi.portal_app', {
            'init_data': json.dumps({'anggota': anggota._portal_identitas()}),
        })

    # ------------------------------------------------------------------
    # PWA: manifest dan service worker (anggota bisa memasang portal sebagai aplikasi)
    # ------------------------------------------------------------------
    @http.route('/koperasi/portal/manifest.json', type='http', auth='public', website=False)
    def manifest(self, **kw):
        ikon = '/koperasi/static/src/portal/icons/'
        versi = get_manifest('koperasi').get('version', '0')
        isi = {
            'id': '/koperasi/portal',
            'name': 'Koperasi - Portal Anggota',
            'short_name': 'Koperasi',
            'description': 'Simpanan, pinjaman, dan tagihan anggota koperasi.',
            'start_url': '/koperasi/portal',
            'scope': '/koperasi/portal',
            'display': 'standalone',
            'orientation': 'portrait-primary',
            'background_color': '#ffffff',
            'theme_color': '#71639e',
            'lang': 'id',
            'categories': ['finance'],
            'icons': [
                {'src': '%sicon-192.png?v=%s' % (ikon, versi), 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
                {'src': '%sicon-512.png?v=%s' % (ikon, versi), 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
                {'src': '%sicon-maskable-512.png?v=%s' % (ikon, versi), 'sizes': '512x512', 'type': 'image/png',
                 'purpose': 'maskable'},
            ],
            'shortcuts': [
                {'name': 'Simpanan', 'url': '/koperasi/portal#simpanan',
                 'icons': [{'src': '%sicon-192.png?v=%s' % (ikon, versi), 'sizes': '192x192'}]},
                {'name': 'Pinjaman', 'url': '/koperasi/portal#pinjaman',
                 'icons': [{'src': '%sicon-192.png?v=%s' % (ikon, versi), 'sizes': '192x192'}]},
            ],
        }
        return request.make_response(json.dumps(isi), headers=[
            ('Content-Type', 'application/manifest+json'), ('Cache-Control', 'no-cache')])

    @http.route('/koperasi/portal/sw.js', type='http', auth='public', website=False)
    def service_worker(self, **kw):
        """Service worker portal. Tugasnya sengaja sedikit:
        - menyimpan berkas statis (OWL, gaya, skrip, ikon) supaya aplikasi cepat terbuka;
        - menampilkan layar "tidak ada koneksi" saat offline;
        - pada server multi-database: bila sesi habis dan halaman portal menjawab 404 (Odoo tidak
          tahu databasenya), pengunjung dialihkan lewat pintu masuk yang menetapkan database.
        Data (API) dan halaman HTML TIDAK pernah disimpan, jadi angka selalu dari server."""
        versi = get_manifest('koperasi').get('version', '0')
        try:
            banyak_db = len(db_list(force=True, host=request.httprequest.environ['HTTP_HOST'])) > 1
        except Exception:
            banyak_db = False
        pintu = '/web/login?db=%s&redirect=%%2Fkoperasi%%2Fportal' % request.db if banyak_db else ''
        statis = '/koperasi/static/src/portal/'
        kerangka = [
            '/web/static/lib/owl/owl.js',
            '/web/static/src/libs/fontawesome/css/font-awesome.css',
            '%sportal.css?v=%s' % (statis, versi), '%sutil.js?v=%s' % (statis, versi),
            '%spwa.js?v=%s' % (statis, versi), '%sapp.js?v=%s' % (statis, versi),
            '%sicons/icon-192.png?v=%s' % (statis, versi),
        ]
        isi = SERVICE_WORKER % {
            'versi': json.dumps('koperasi-portal-' + versi), 'pintu': json.dumps(pintu),
            'kerangka': json.dumps(kerangka),
        }
        return request.make_response(isi, headers=[
            ('Content-Type', 'application/javascript; charset=utf-8'),
            ('Service-Worker-Allowed', '/koperasi/'),
            ('Cache-Control', 'no-cache'),
        ])

    # ------------------------------------------------------------------
    # API data (JSON-RPC)
    # ------------------------------------------------------------------
    @http.route('/koperasi/portal/api/beranda', type='jsonrpc', auth='public')
    def api_beranda(self, **kw):
        return self._api(lambda a: a._portal_beranda())

    @http.route('/koperasi/portal/api/simpanan', type='jsonrpc', auth='public')
    def api_simpanan(self, **kw):
        return self._api(lambda a: a._portal_simpanan())

    @http.route('/koperasi/portal/api/pinjaman', type='jsonrpc', auth='public')
    def api_pinjaman(self, **kw):
        return self._api(lambda a: a._portal_pinjaman())

    @http.route('/koperasi/portal/api/profil', type='jsonrpc', auth='public')
    def api_profil(self, **kw):
        return self._api(lambda a: a._portal_profil())

    @http.route('/koperasi/portal/api/foto', type='jsonrpc', auth='public')
    def api_foto(self, foto=None, **kw):
        def simpan(anggota):
            data = _foto_valid(foto)
            if not data:
                raise UserError('Pilih foto terlebih dahulu.')
            anggota.write({'image_1920': data})
            return anggota._portal_identitas()
        return self._api(simpan)

    @http.route('/koperasi/portal/api/password', type='jsonrpc', auth='public')
    def api_password(self, lama=None, baru=None, **kw):
        def ganti(anggota):
            if not _boleh('password'):
                raise UserError('Terlalu banyak percobaan. Coba lagi nanti.')
            request.env['koperasi.anggota']._cek_password(baru)
            try:
                request.env.user.change_password(lama, baru)
            except AccessDenied:
                raise UserError('Password lama tidak sesuai.')
            # Token sesi bergantung pada password; perbarui supaya pengguna tidak langsung keluar
            # (sama seperti yang dilakukan Odoo saat memperbarui hash password pengguna sendiri).
            request.env.flush_all()
            request.env.registry.clear_cache()
            request.session.session_token = request.env.user._compute_session_token(request.session.sid)
            return True
        return self._api(ganti)

    # ------------------------------------------------------------------
    # Berkas: foto profil dan PDF
    # ------------------------------------------------------------------
    @http.route('/koperasi/portal/foto', type='http', auth='public', website=False)
    def foto(self, **kw):
        anggota = self._anggota()
        if not anggota:
            raise request.not_found()
        return request.env['ir.binary']._get_image_stream_from(anggota, 'avatar_256').get_response()

    def _pdf(self, anggota, params):
        isi, _tipe = request.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'koperasi.laporan_pdf', data={'params': params})
        Laporan = request.env['koperasi.laporan'].sudo()
        laporan = Laporan.get_laporan(
            params['jenis'], False, False, False, params.get('anggota') or False, params.get('pinjaman') or False)
        return request.make_response(isi, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Disposition', content_disposition(Laporan.nama_file(laporan, 'pdf'), 'inline')),
            ('Content-Length', len(isi)),
        ])

    @http.route('/koperasi/portal/buku.pdf', type='http', auth='public', website=False)
    def buku_pdf(self, **kw):
        anggota = self._anggota()
        if not anggota:
            return request.redirect('/koperasi/portal/login')
        return self._pdf(anggota, {'jenis': 'anggota', 'anggota': anggota.id})

    @http.route('/koperasi/portal/kartu/<int:pinjaman_id>.pdf', type='http', auth='public', website=False)
    def kartu_pdf(self, pinjaman_id, **kw):
        anggota = self._anggota()
        if not anggota:
            return request.redirect('/koperasi/portal/login')
        # hanya pinjaman milik anggota ini
        if pinjaman_id not in anggota.pinjaman_ids.filtered(lambda p: p.state not in ('draft', 'ditolak')).ids:
            raise request.not_found()
        return self._pdf(anggota, {'jenis': 'kartu', 'pinjaman': pinjaman_id})
