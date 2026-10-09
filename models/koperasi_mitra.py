from odoo import api, fields, models, _
from odoo.addons.base.models.ir_mail_server import MailDeliveryException
from odoo.exceptions import AccessError, UserError
from odoo.fields import Command
from odoo.tools import email_normalize

ADMIN_FIELDS = ('admin_nama', 'admin_email', 'admin_telepon')


class KoperasiMitra(models.Model):
    _name = 'koperasi.mitra'
    _description = 'Mitra Koperasi'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'koperasi.kontak.mixin',
                'koperasi.mata.uang.mixin']
    _order = 'name'

    name = fields.Char('Nama Mitra', required=True, index=True, tracking=True)
    kode = fields.Char(
        'Kode Mitra', readonly=True, copy=False, index=True,
        help='Dibuat otomatis saat mitra disimpan (MTR-001, MTR-002, ...).')
    active = fields.Boolean(default=True, tracking=True)
    # Kontak perusahaan, alamat, telepon, email: lihat koperasi.kontak.mixin.
    npwp = fields.Char('NPWP')
    penanggung_jawab = fields.Char('Penanggung Jawab / HRD')
    tanggal_bergabung = fields.Date('Tanggal Bergabung')
    catatan = fields.Text('Catatan')

    # Admin mitra: diisi operator di form mitra; akun user-nya baru dibuat
    # saat undangan dikirim (action_kirim_undangan).
    admin_user_id = fields.Many2one(
        'res.users', string='Akun Admin Mitra', readonly=True, copy=False,
        ondelete='set null')
    admin_nama = fields.Char('Nama', copy=False, tracking=True)
    admin_email = fields.Char('Email Login', copy=False, tracking=True)
    admin_telepon = fields.Char('Telp / WA', copy=False)
    admin_status = fields.Selection(
        [('belum', 'Belum Diundang'),
         ('diundang', 'Menunggu Aktivasi'),
         ('aktif', 'Aktif'),
         ('nonaktif', 'Nonaktif')],
        string='Status Akun', compute='_compute_admin_status')

    admin_ids = fields.One2many('res.users', 'koperasi_mitra_id', string='Admin Mitra')
    anggota_ids = fields.One2many('koperasi.anggota', 'mitra_id', string='Anggota')
    anggota_count = fields.Integer('Jumlah Anggota', compute='_compute_anggota_count')

    # Ringkasan untuk smart button; dihitung dengan hak pengguna yang membuka form,
    # jadi admin mitra hanya melihat data mitranya dan tagihan yang sudah ditagihkan.
    anggota_menunggu_count = fields.Integer('Menunggu Validasi', compute='_compute_ringkasan')
    saldo_simpanan = fields.Monetary('Total Simpanan', compute='_compute_ringkasan')
    pinjaman_berjalan_count = fields.Integer('Pinjaman Berjalan', compute='_compute_ringkasan')
    pinjaman_sisa = fields.Monetary('Sisa Pinjaman', compute='_compute_ringkasan')
    pinjaman_pengajuan_count = fields.Integer('Pengajuan Pinjaman', compute='_compute_ringkasan')
    tagihan_count = fields.Integer('Tagihan', compute='_compute_ringkasan')
    tunggakan_total = fields.Monetary('Tunggakan', compute='_compute_ringkasan')

    _kode_uniq = models.Constraint('UNIQUE(kode)', 'Kode mitra sudah dipakai.')

    @api.depends('anggota_ids')
    def _compute_anggota_count(self):
        data = self.env['koperasi.anggota']._read_group(
            [('mitra_id', 'in', self.ids)], ['mitra_id'], ['__count'])
        counts = {mitra.id: n for mitra, n in data}
        for r in self:
            r.anggota_count = counts.get(r.id, 0)

    def _compute_ringkasan(self):
        Anggota = self.env['koperasi.anggota']
        Pinjaman = self.env['koperasi.pinjaman']
        Tagihan = self.env['koperasi.tagihan']
        Line = self.env['koperasi.tagihan.line']
        ids = self.ids
        menunggu = {m.id: n for m, n in Anggota._read_group(
            [('mitra_id', 'in', ids), ('state', '=', 'menunggu')], ['mitra_id'], ['__count'])}
        saldo = {m.id: (p or 0) + (w or 0) + (s or 0) for m, p, w, s in Line._read_group(
            [('mitra_id', 'in', ids), ('status', '=', 'dibayar')], ['mitra_id'],
            ['simpok:sum', 'simwa:sum', 'simsula:sum'])}
        berjalan = {m.id: (n, sisa or 0) for m, n, sisa in Pinjaman._read_group(
            [('mitra_id', 'in', ids), ('state', '=', 'berjalan')], ['mitra_id'],
            ['__count', 'sisa_pokok:sum'])}
        pengajuan = {m.id: n for m, n in Pinjaman._read_group(
            [('mitra_id', 'in', ids), ('state', 'in', ('diajukan', 'disetujui'))],
            ['mitra_id'], ['__count'])}
        tagihan = {m.id: n for m, n in Tagihan._read_group(
            [('mitra_id', 'in', ids)], ['mitra_id'], ['__count'])}
        # Tunggakan = baris Belum Dibayar yang belum ditagih ulang (nilainya utuh)
        # + bagian tunggakan pada baris yang sudah memuatnya tetapi belum divalidasi.
        tunggakan = {}
        for m, t in Line._read_group(
                [('mitra_id', 'in', ids), ('status', '=', 'belum'), ('dibawa_ke_id', '=', False)],
                ['mitra_id'], ['total:sum']):
            tunggakan[m.id] = tunggakan.get(m.id, 0) + (t or 0)
        for m, t in Line._read_group(
                [('mitra_id', 'in', ids), ('status', '=', 'menunggu')],
                ['mitra_id'], ['tunggakan:sum']):
            tunggakan[m.id] = tunggakan.get(m.id, 0) + (t or 0)
        for r in self:
            r.anggota_menunggu_count = menunggu.get(r.id, 0)
            r.saldo_simpanan = saldo.get(r.id, 0)
            r.pinjaman_berjalan_count, r.pinjaman_sisa = berjalan.get(r.id, (0, 0))
            r.pinjaman_pengajuan_count = pengajuan.get(r.id, 0)
            r.tagihan_count = tagihan.get(r.id, 0)
            r.tunggakan_total = tunggakan.get(r.id, 0)

    @api.depends('admin_user_id', 'admin_user_id.active')
    def _compute_admin_status(self):
        for r in self:
            user = r.admin_user_id.sudo()
            if not user:
                r.admin_status = 'belum'
            elif not user.active:
                r.admin_status = 'nonaktif'
            else:
                r.admin_status = 'aktif' if user.login_date else 'diundang'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('kode'):
                vals['kode'] = self.env['ir.sequence'].next_by_code('koperasi.mitra')
        return super().create(vals_list)

    def write(self, vals):
        res = super().write(vals)
        if set(ADMIN_FIELDS) & vals.keys():
            for r in self.sudo().filtered('admin_user_id'):
                r.admin_user_id.write(r._admin_user_vals())
        return res

    def _partner_field_map(self):
        return dict(super()._partner_field_map(), npwp='vat')

    def _partner_vals_baru(self):
        return {'is_company': True}

    # ------------------------------------------------------------------
    # Admin mitra
    # ------------------------------------------------------------------
    def _admin_user_vals(self):
        self.ensure_one()
        email = email_normalize(self.admin_email or '')
        if not self.admin_nama or not email:
            raise UserError(_('Nama dan email login admin mitra wajib diisi dengan benar.'))
        dobel = self.env['res.users'].sudo().with_context(active_test=False).search_count(
            [('login', '=', email), ('id', '!=', self.admin_user_id.id)], limit=1)
        if dobel:
            raise UserError(_('Email %s sudah dipakai akun lain.', email))
        return {
            'name': self.admin_nama,
            'login': email,
            'email': email,
            'phone': self.admin_telepon,
        }

    def action_kirim_undangan(self):
        """Buat akun admin mitra bila belum ada, lalu kirim email undangan.

        sudo: operator bukan administrator sistem sehingga tidak berhak
        membuat res.users. Aman karena grup akun dikunci ke Admin Mitra dan
        mitranya dikunci ke record ini.
        """
        self.ensure_one()
        if not self.env.user.has_group('koperasi.group_koperasi_operator'):
            raise AccessError(_('Hanya operator pusat yang dapat mengundang admin mitra.'))
        mitra = self.sudo()
        vals = mitra._admin_user_vals()
        user = mitra.admin_user_id
        if not user:
            user = self.env['res.users'].sudo().with_context(no_reset_password=True).create(dict(
                vals,
                group_ids=[Command.set(self.env.ref('koperasi.group_koperasi_mitra').ids)],
                koperasi_mitra_id=self.id,
            ))
            mitra.admin_user_id = user
        elif mitra.admin_status != 'diundang':
            raise UserError(_('Akun admin mitra ini sudah aktif atau dinonaktifkan.'))

        try:
            user.with_context(create_user=True)._action_reset_password(signup_type='signup')
        except MailDeliveryException:
            # Akun tetap dibuat; undangan bisa dikirim ulang setelah server
            # email keluar dibenahi.
            tipe = 'warning'
            pesan = _('Akun %s sudah dibuat, tetapi email undangan gagal dikirim. '
                      'Periksa pengaturan server email keluar lalu kirim ulang.', user.login)
        else:
            tipe = 'success'
            pesan = _('Undangan dikirim ke %s.', user.login)
            mitra.message_post(body=pesan)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': tipe,
                'message': pesan,
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def action_lihat_anggota(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Anggota %s', self.name),
            'res_model': 'koperasi.anggota',
            'view_mode': 'list,form',
            'domain': [('mitra_id', '=', self.id)],
            'context': {'default_mitra_id': self.id},
        }

    def _aksi_daftar(self, nama, model, domain, mode='list,form', context=None):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': '%s - %s' % (nama, self.name),
            'res_model': model,
            'view_mode': mode,
            'domain': domain,
            'context': dict(context or {}),
        }

    def action_lihat_anggota_menunggu(self):
        return self._aksi_daftar(
            self.env._('Menunggu Validasi'), 'koperasi.anggota',
            [('mitra_id', '=', self.id), ('state', '=', 'menunggu')],
            context={'default_mitra_id': self.id})

    def action_lihat_simpanan(self):
        """Anggota aktif mitra ini; daftar anggota menampilkan kolom saldo simpanan."""
        return self._aksi_daftar(
            self.env._('Simpanan Anggota'), 'koperasi.anggota',
            [('mitra_id', '=', self.id), ('state', '=', 'aktif')])

    def action_lihat_pinjaman(self):
        return self._aksi_daftar(
            self.env._('Pinjaman'), 'koperasi.pinjaman', [('mitra_id', '=', self.id)],
            context={'search_default_f_berjalan': 1})

    def action_lihat_pinjaman_pengajuan(self):
        return self._aksi_daftar(
            self.env._('Pengajuan Pinjaman'), 'koperasi.pinjaman',
            [('mitra_id', '=', self.id), ('state', 'in', ('diajukan', 'disetujui'))])

    def action_lihat_tagihan(self):
        return self._aksi_daftar(
            self.env._('Tagihan'), 'koperasi.tagihan', [('mitra_id', '=', self.id)])

    def action_lihat_tunggakan(self):
        return self._aksi_daftar(
            self.env._('Tunggakan'), 'koperasi.tagihan.line',
            [('mitra_id', '=', self.id),
             '|', '&', ('status', '=', 'belum'), ('dibawa_ke_id', '=', False),
             '&', ('status', '=', 'menunggu'), ('tunggakan', '>', 0)],
            mode='list')
