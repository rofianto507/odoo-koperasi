{
    'name': 'Koperasi',
    'version': '19.0.4.1.0',
    'summary': 'Manajemen koperasi simpan pinjam pegawai lintas mitra',
    'description': """
Platform simpan pinjam koperasi pegawai. Operator pusat mengelola mitra
(perusahaan) dan memvalidasi anggota; admin tiap mitra mendaftarkan pegawainya
sebagai calon anggota koperasi.
""",
    'category': 'Koperasi',
    'author': 'Cv Sel Studio',
    'website': 'https://selstudio.id',
    'depends': ['base', 'mail', 'web', 'auth_signup'],
    'data': [
        'security/koperasi_security.xml',
        'security/ir.model.access.csv',
        'security/koperasi_rule.xml',
        'data/koperasi_sequence_data.xml',
        'data/koperasi_simpanan_data.xml',
        'views/koperasi_mitra_views.xml',
        'views/koperasi_anggota_views.xml',
        'views/koperasi_simpanan_views.xml',
        'views/koperasi_pinjaman_views.xml',
        'views/koperasi_dashboard_views.xml',
        'views/koperasi_laporan_views.xml',
        'views/res_users_views.xml',
        'wizard/koperasi_tolak_wizard_views.xml',
        'wizard/koperasi_buat_tagihan_wizard_views.xml',
        'report/koperasi_laporan_report.xml',
        'views/menu.xml',
        'views/koperasi_pembukuan_views.xml',
        'views/koperasi_penarikan_views.xml',
        'views/koperasi_shu_views.xml',
        'views/koperasi_portal_templates.xml',
        'data/koperasi_pembukuan_data.xml',
    ],
    'assets': {
        'web.assets_backend': [
            # ECharts (static/lib/echarts) dimuat saat dashboard dibuka, lewat loadJS
            'koperasi/static/src/dashboard/dashboard.css',
            'koperasi/static/src/dashboard/dashboard.xml',
            'koperasi/static/src/dashboard/dashboard.js',
            'koperasi/static/src/laporan/laporan.css',
            'koperasi/static/src/laporan/laporan.xml',
            'koperasi/static/src/laporan/laporan.js',
        ],
    },
    'installable': True,
    'application': True,
    'sequence': 6,
    'license': 'LGPL-3',
}
