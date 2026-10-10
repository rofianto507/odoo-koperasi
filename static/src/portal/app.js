/* Koperasi — Portal Anggota: aplikasi utama (OWL).
   Empat halaman dengan navigasi bawah: Beranda, Simpanan, Pinjaman, Profil.
   Tiap halaman memuat datanya sendiri saat dibuka, jadi angka selalu yang terbaru. */
(function () {
    'use strict';

    const { Component, useState, mount, xml, onWillStart, onWillUnmount } = owl;
    const { rpc, rp, tgl, inisial, warnaStatus, kecilkanFoto, syaratPassword } = window.KopPortal;

    const MASUK = '/koperasi/portal/login';

    /** Dasar tiap halaman: pemuatan data, pemformat, dan penanganan sesi habis. */
    class Halaman extends Component {
        static props = ['*'];
        url = null;

        setup() {
            this.state = useState({ muat: true, galat: null, data: null });
            this.siapkan();
            onWillStart(() => this.muat());
        }

        siapkan() {}

        async panggil(url, params) {
            const hasil = await rpc(url, params);
            if (hasil.error === 'auth') {
                window.location.href = MASUK;
                return new Promise(() => {});  // halaman berpindah; hentikan alur
            }
            if (hasil.error) throw new Error(hasil.pesan || 'Terjadi kesalahan.');
            return hasil.data;
        }

        async muat() {
            this.state.muat = true;
            this.state.galat = null;
            try {
                this.state.data = await this.panggil(this.url);
            } catch (e) {
                this.state.galat = e.message;
            }
            this.state.muat = false;
        }

        rp(n) { return rp(n); }
        tgl(t) { return tgl(t); }
        lencana(status) { return 'kp-lencana kp-lencana--' + warnaStatus(status); }
        tanda(n) { return n < 0 ? 'kp-item-nilai kp-minus' : 'kp-item-nilai kp-plus'; }
        bertanda(n) { return (n > 0 ? '+' : '') + rp(n); }
    }

    const MUAT = /* xml */`
        <div t-if="state.muat and !state.data" class="kp-muat"><i class="fa fa-spinner fa-spin"/></div>
        <div t-elif="state.galat" class="kp-isi">
            <div class="kp-peringatan kp-peringatan--merah"><i class="fa fa-exclamation-circle"/><span t-esc="state.galat"/></div>
            <button class="kp-tombol kp-tombol--garis" t-on-click="muat"><i class="fa fa-refresh"/> Coba Lagi</button>
        </div>`;

    // ── Avatar ───────────────────────────────────────────────────────────────
    class Avatar extends Component {
        static props = ['*'];
        static template = xml/* xml */`
            <div t-attf-class="kp-avatar #{props.kelas or ''}">
                <img t-if="props.anggota.foto and !state.gagal" t-att-src="props.anggota.foto" alt="Foto profil"
                     t-on-error="() => this.state.gagal = true"/>
                <t t-else="" t-esc="huruf"/>
            </div>`;
        setup() { this.state = useState({ gagal: false }); }
        get huruf() { return inisial(this.props.anggota.nama); }
    }

    // ── Beranda ──────────────────────────────────────────────────────────────
    class Beranda extends Halaman {
        static components = { Avatar };
        url = '/koperasi/portal/api/beranda';
        static template = xml/* xml */`
            <div>
                <div class="kp-hero">
                    <div class="kp-hero-baris">
                        <Avatar anggota="props.anggota"/>
                        <div class="kp-hero-teks">
                            <div class="kp-hero-salam">Halo,</div>
                            <div class="kp-hero-nama" t-esc="props.anggota.nama"/>
                            <div class="kp-hero-sub">
                                <t t-if="props.anggota.nomor"><t t-esc="props.anggota.nomor"/> · </t>
                                <t t-esc="props.anggota.mitra"/>
                            </div>
                        </div>
                    </div>
                </div>
                ${MUAT}
                <div t-if="state.data" class="kp-isi kp-isi--naik">
                    <t t-set="d" t-value="state.data"/>

                    <div class="kp-kartu kp-kartu--angkat">
                        <div class="kp-baris">
                            <div style="min-width:0">
                                <div class="kp-label">Total Simpanan</div>
                                <div class="kp-nilai" t-esc="state.sembunyi ? 'Rp ••••••' : rp(d.saldo.total)"/>
                            </div>
                            <button class="kp-mata" t-on-click="() => this.state.sembunyi = !this.state.sembunyi"
                                    t-att-title="state.sembunyi ? 'Tampilkan saldo' : 'Sembunyikan saldo'">
                                <i t-attf-class="fa #{ state.sembunyi ? 'fa-eye' : 'fa-eye-slash' }"/>
                            </button>
                        </div>
                        <div class="kp-tiga">
                            <div class="kp-mini"><div class="kp-kecil">Pokok</div>
                                <div class="kp-mini-nilai" t-esc="state.sembunyi ? '•••' : rp(d.saldo.pokok)"/></div>
                            <div class="kp-mini"><div class="kp-kecil">Wajib</div>
                                <div class="kp-mini-nilai" t-esc="state.sembunyi ? '•••' : rp(d.saldo.wajib)"/></div>
                            <div class="kp-mini"><div class="kp-kecil">Sukarela</div>
                                <div class="kp-mini-nilai" t-esc="state.sembunyi ? '•••' : rp(d.saldo.sukarela)"/></div>
                        </div>
                    </div>

                    <div t-if="d.anggota.status === 'keluar'" class="kp-peringatan kp-peringatan--info">
                        <i class="fa fa-info-circle"/>
                        <span>Anda sudah keluar dari keanggotaan. Riwayat simpanan dan pinjaman tetap dapat dilihat.</span>
                    </div>
                    <div t-if="d.tunggakan > 0" class="kp-peringatan kp-peringatan--merah">
                        <i class="fa fa-exclamation-triangle"/>
                        <span>Anda punya tunggakan <b t-esc="rp(d.tunggakan)"/>. Tunggakan ditagihkan kembali pada tagihan bulan berikutnya.</span>
                    </div>

                    <div class="kp-grid-lebar">
                        <div class="kp-kartu">
                            <div class="kp-kartu-judul">
                                <span><i class="fa fa-file-text-o"/> Tagihan <t t-if="d.tagihan" t-esc="d.tagihan.periode"/></span>
                                <span t-if="d.tagihan" t-att-class="lencana(d.tagihan.status)" t-esc="d.tagihan.status_label"/>
                            </div>
                            <t t-if="d.tagihan">
                                <div class="kp-nilai kp-nilai--sedang" t-esc="rp(d.tagihan.total)"/>
                                <div class="kp-kecil" style="margin-top:4px">
                                    simpanan <t t-esc="rp(d.tagihan.simpanan)"/>
                                    <t t-if="d.tagihan.angsuran"> · angsuran <t t-esc="rp(d.tagihan.angsuran)"/></t>
                                    <t t-if="d.tagihan.tunggakan"> · tunggakan <t t-esc="rp(d.tagihan.tunggakan)"/></t>
                                </div>
                                <div class="kp-kecil" style="margin-top:6px">Dipotong dari gaji melalui tempat kerja Anda.</div>
                            </t>
                            <div t-else="" class="kp-kecil">Belum ada tagihan.</div>
                        </div>

                        <div class="kp-kartu kp-kartu--tekan" t-on-click="() => props.pindah('pinjaman')">
                            <div class="kp-kartu-judul">
                                <span><i class="fa fa-credit-card"/> Pinjaman</span>
                                <i class="fa fa-chevron-right kp-kecil"/>
                            </div>
                            <t t-if="d.pinjaman">
                                <div class="kp-baris">
                                    <div>
                                        <div class="kp-kecil">Sisa pokok</div>
                                        <div class="kp-nilai kp-nilai--sedang" t-esc="rp(d.pinjaman.sisa)"/>
                                    </div>
                                    <div style="text-align:right">
                                        <div class="kp-kecil">Angsuran / bulan</div>
                                        <div style="font-weight:700" t-esc="rp(d.pinjaman.angsuran)"/>
                                    </div>
                                </div>
                                <div class="kp-batang"><div t-attf-style="width: #{d.pinjaman.persen}%"/></div>
                                <div class="kp-kecil">
                                    <t t-esc="d.pinjaman.dibayar"/> dari <t t-esc="d.pinjaman.tenor"/> angsuran dibayar
                                </div>
                            </t>
                            <div t-else="" class="kp-kecil">
                                Tidak ada pinjaman berjalan.
                                <t t-if="d.pengajuan"> <t t-esc="d.pengajuan"/> pengajuan sedang diproses.</t>
                            </div>
                        </div>
                    </div>

                    <div class="kp-kartu">
                        <div class="kp-kartu-judul"><span><i class="fa fa-history"/> Aktivitas Terbaru</span></div>
                        <div t-if="d.aktivitas.length" class="kp-daftar">
                            <t t-foreach="d.aktivitas" t-as="a" t-key="a_index">
                                <div class="kp-item">
                                    <div t-attf-class="kp-item-ikon kp-item-ikon--#{a.warna}"><i t-attf-class="fa #{a.ikon}"/></div>
                                    <div class="kp-item-isi">
                                        <div class="kp-item-judul" t-esc="a.judul"/>
                                        <div class="kp-kecil"><t t-esc="tgl(a.tanggal)"/> · <t t-esc="a.sub"/></div>
                                    </div>
                                    <div t-att-class="a.nilai &lt; 0 ? 'kp-item-nilai kp-minus' : 'kp-item-nilai'" t-esc="rp(a.nilai)"/>
                                </div>
                            </t>
                        </div>
                        <div t-else="" class="kp-kosong"><i class="fa fa-inbox"/>Belum ada aktivitas.</div>
                    </div>
                </div>
            </div>`;

        siapkan() { this.state.sembunyi = false; }
    }

    // ── Simpanan ─────────────────────────────────────────────────────────────
    class Simpanan extends Halaman {
        url = '/koperasi/portal/api/simpanan';
        static template = xml/* xml */`
            <div>
                <div class="kp-bar"><i class="fa fa-bank"/> Simpanan</div>
                ${MUAT}
                <div t-if="state.data" class="kp-isi">
                    <t t-set="d" t-value="state.data"/>
                    <div class="kp-kartu kp-kartu--angkat">
                        <div class="kp-label">Total Simpanan</div>
                        <div class="kp-nilai" t-esc="rp(d.saldo.total)"/>
                        <div class="kp-tiga">
                            <div class="kp-mini"><div class="kp-kecil">Pokok</div><div class="kp-mini-nilai" t-esc="rp(d.saldo.pokok)"/></div>
                            <div class="kp-mini"><div class="kp-kecil">Wajib</div><div class="kp-mini-nilai" t-esc="rp(d.saldo.wajib)"/></div>
                            <div class="kp-mini"><div class="kp-kecil">Sukarela</div><div class="kp-mini-nilai" t-esc="rp(d.saldo.sukarela)"/></div>
                        </div>
                        <div t-if="d.bulanan.wajib or d.bulanan.sukarela" class="kp-kecil" style="margin-top:12px">
                            Setoran tiap bulan: wajib <b t-esc="rp(d.bulanan.wajib)"/>
                            <t t-if="d.bulanan.sukarela"> · sukarela <b t-esc="rp(d.bulanan.sukarela)"/></t>
                        </div>
                    </div>

                    <a class="kp-tombol kp-tombol--garis" href="/koperasi/portal/buku.pdf" target="_blank" rel="noopener">
                        <i class="fa fa-file-pdf-o"/> Unduh Buku Simpanan (PDF)
                    </a>

                    <div class="kp-segmen">
                        <button t-att-class="state.tab === 'mutasi' ? 'aktif' : ''" t-on-click="() => this.state.tab = 'mutasi'">Mutasi</button>
                        <button t-att-class="state.tab === 'penarikan' ? 'aktif' : ''" t-on-click="() => this.state.tab = 'penarikan'">
                            Penarikan<t t-if="d.penarikan.length"> (<t t-esc="d.penarikan.length"/>)</t>
                        </button>
                    </div>

                    <div t-if="state.tab === 'mutasi'" class="kp-kartu">
                        <div t-if="d.mutasi.length" class="kp-daftar">
                            <t t-foreach="d.mutasi" t-as="m" t-key="m_index">
                                <div class="kp-item">
                                    <div class="kp-item-isi">
                                        <div class="kp-item-judul" t-esc="m.periode"/>
                                        <div class="kp-kecil">
                                            <span t-att-class="lencana(m.status)" t-esc="m.status"/>
                                            saldo <t t-esc="rp(m.saldo)"/>
                                        </div>
                                    </div>
                                    <div t-att-class="m.mutasi ? tanda(m.mutasi) : 'kp-item-nilai kp-kecil'" t-esc="m.mutasi ? bertanda(m.mutasi) : rp(0)"/>
                                </div>
                            </t>
                        </div>
                        <div t-else="" class="kp-kosong"><i class="fa fa-inbox"/>Belum ada mutasi simpanan.</div>
                    </div>

                    <div t-else="" class="kp-kartu">
                        <div t-if="d.penarikan.length" class="kp-daftar">
                            <t t-foreach="d.penarikan" t-as="t" t-key="t.nomor">
                                <div class="kp-item">
                                    <div class="kp-item-isi">
                                        <div class="kp-item-judul" t-esc="t.jenis"/>
                                        <div class="kp-kecil">
                                            <t t-esc="t.nomor"/> · <t t-esc="tgl(t.tanggal)"/>
                                        </div>
                                        <span t-att-class="lencana(t.status)" t-esc="t.status_label"/>
                                    </div>
                                    <div class="kp-item-nilai" t-esc="rp(t.total)"/>
                                </div>
                            </t>
                        </div>
                        <div t-else="" class="kp-kosong"><i class="fa fa-inbox"/>Belum ada penarikan simpanan.</div>
                        <div class="kp-kecil" style="margin-top:10px">
                            Penarikan simpanan diajukan melalui admin di tempat kerja Anda.
                        </div>
                    </div>
                </div>
            </div>`;

        siapkan() { this.state.tab = 'mutasi'; }
    }

    // ── Pinjaman ─────────────────────────────────────────────────────────────
    class Pinjaman extends Halaman {
        url = '/koperasi/portal/api/pinjaman';
        static template = xml/* xml */`
            <div>
                <div class="kp-bar"><i class="fa fa-credit-card"/> Pinjaman</div>
                ${MUAT}
                <div t-if="state.data" class="kp-isi">
                    <div t-if="!state.data.daftar.length" class="kp-kartu kp-kosong">
                        <i class="fa fa-credit-card"/>Anda belum pernah mengajukan pinjaman.
                        <div class="kp-kecil" style="margin-top:8px">Pinjaman diajukan melalui admin di tempat kerja Anda.</div>
                    </div>
                    <t t-foreach="state.data.daftar" t-as="p" t-key="p.id">
                        <div class="kp-kartu">
                            <div class="kp-kartu-judul">
                                <span t-esc="p.nomor"/>
                                <span t-att-class="lencana(p.status)" t-esc="p.status_label"/>
                            </div>
                            <div class="kp-baris">
                                <div>
                                    <div class="kp-kecil" t-esc="p.status === 'berjalan' ? 'Sisa pokok' : 'Pokok pinjaman'"/>
                                    <div class="kp-nilai kp-nilai--sedang" t-esc="rp(p.status === 'berjalan' ? p.sisa : p.pokok)"/>
                                </div>
                                <div style="text-align:right">
                                    <div class="kp-kecil">Angsuran / bulan</div>
                                    <div style="font-weight:700" t-esc="rp(p.angsuran)"/>
                                </div>
                            </div>
                            <t t-if="p.jadwal.length">
                                <div class="kp-batang"><div t-attf-style="width: #{p.persen}%"/></div>
                                <div class="kp-baris kp-kecil">
                                    <span><t t-esc="p.dibayar"/> dari <t t-esc="p.tenor"/> angsuran dibayar</span>
                                    <span t-if="p.kualitas" t-att-class="lencana(p.kualitas)" t-esc="p.kualitas_label"/>
                                </div>
                            </t>
                            <div t-if="p.alasan_tolak" class="kp-peringatan kp-peringatan--merah" style="margin-top:10px">
                                <i class="fa fa-times-circle"/><span t-esc="p.alasan_tolak"/>
                            </div>

                            <button class="kp-tombol kp-tombol--garis kp-tombol--kecil" style="margin-top:12px" t-on-click="() => this.buka(p.id)">
                                <i t-attf-class="fa #{ state.terbuka === p.id ? 'fa-chevron-up' : 'fa-chevron-down' }"/>
                                <t t-esc="state.terbuka === p.id ? 'Tutup Rincian' : 'Lihat Rincian'"/>
                            </button>

                            <div t-if="state.terbuka === p.id" style="margin-top:14px">
                                <div class="kp-info"><span>Produk</span><span t-esc="p.produk"/></div>
                                <div t-if="p.keperluan" class="kp-info"><span>Keperluan</span><span t-esc="p.keperluan"/></div>
                                <div class="kp-info"><span>Pokok pinjaman</span><span t-esc="rp(p.pokok)"/></div>
                                <div class="kp-info"><span>Tenor</span><span><t t-esc="p.tenor"/> bulan</span></div>
                                <div class="kp-info"><span>Jasa per bulan</span><span t-esc="rp(p.jasa_bulan)"/></div>
                                <div class="kp-info"><span>Biaya admin</span><span t-esc="rp(p.biaya_admin)"/></div>
                                <div class="kp-info"><span>Diterima</span><span t-esc="rp(p.diterima)"/></div>
                                <div t-if="p.tanggal_cair" class="kp-info"><span>Dicairkan</span><span t-esc="tgl(p.tanggal_cair)"/></div>

                                <t t-if="p.jadwal.length">
                                    <div class="kp-seksi" style="margin:16px 0 6px">Jadwal Angsuran</div>
                                    <table class="kp-tabel">
                                        <thead><tr><th>Ke</th><th>Periode</th><th class="kp-kanan">Jumlah</th><th class="kp-kanan">Status</th></tr></thead>
                                        <tbody>
                                            <t t-foreach="p.jadwal" t-as="j" t-key="j.ke">
                                                <tr>
                                                    <td t-esc="j.ke"/>
                                                    <td t-esc="j.periode"/>
                                                    <td class="kp-kanan" t-esc="rp(j.total)"/>
                                                    <td class="kp-kanan"><span t-att-class="lencana(j.status)" t-esc="j.status_label"/></td>
                                                </tr>
                                            </t>
                                        </tbody>
                                    </table>
                                    <a class="kp-tombol kp-tombol--garis kp-tombol--kecil" style="margin-top:12px"
                                       t-attf-href="/koperasi/portal/kartu/#{p.id}.pdf" target="_blank" rel="noopener">
                                        <i class="fa fa-file-pdf-o"/> Unduh Kartu Pinjaman (PDF)
                                    </a>
                                </t>
                                <div t-elif="p.status !== 'ditolak'" class="kp-kecil" style="margin-top:12px">
                                    Jadwal angsuran tersedia setelah pinjaman dicairkan.
                                </div>
                            </div>
                        </div>
                    </t>
                </div>
            </div>`;

        siapkan() { this.state.terbuka = null; }

        async muat() {
            await super.muat();
            // langsung buka rincian pinjaman yang sedang berjalan
            const d = this.state.data;
            if (d && this.state.terbuka === null) {
                const berjalan = d.daftar.find((p) => p.status === 'berjalan');
                if (berjalan && d.daftar.length === 1) this.state.terbuka = berjalan.id;
            }
        }

        buka(id) { this.state.terbuka = this.state.terbuka === id ? null : id; }
    }

    // ── Profil ───────────────────────────────────────────────────────────────
    class Profil extends Halaman {
        static components = { Avatar };
        url = '/koperasi/portal/api/profil';
        static template = xml/* xml */`
            <div>
                <div class="kp-bar"><i class="fa fa-user-circle"/> Profil</div>
                ${MUAT}
                <div t-if="state.data" class="kp-isi">
                    <t t-set="d" t-value="state.data"/>
                    <div class="kp-kartu kp-kartu--angkat" style="text-align:center">
                        <div class="kp-foto" style="margin-bottom:8px">
                            <div class="kp-foto-lingkar">
                                <Avatar anggota="props.anggota" kelas="'kp-avatar--besar kp-avatar--gelap'" t-key="props.anggota.foto"/>
                                <label class="kp-foto-tombol" title="Ganti foto">
                                    <i t-attf-class="fa #{ state.unggah ? 'fa-spinner fa-spin' : 'fa-camera' }"/>
                                    <input type="file" accept="image/*" t-on-change="gantiFoto"/>
                                </label>
                            </div>
                        </div>
                        <div style="font-size:17px;font-weight:700" t-esc="d.anggota.nama"/>
                        <div class="kp-kecil"><t t-esc="d.anggota.nomor"/> · @<t t-esc="d.username"/></div>
                        <div style="margin-top:6px"><span t-att-class="lencana(d.anggota.status)" t-esc="d.anggota.status_label"/></div>
                    </div>

                    <div class="kp-grid-lebar">
                        <div class="kp-kartu">
                            <div class="kp-kartu-judul"><span><i class="fa fa-id-card-o"/> Data Pribadi</span></div>
                            <t t-foreach="d.pribadi" t-as="b" t-key="b_index">
                                <div class="kp-info"><span t-esc="b[0]"/><span t-esc="b[1]"/></div>
                            </t>
                        </div>
                        <div class="kp-kartu">
                            <div class="kp-kartu-judul"><span><i class="fa fa-building-o"/> Kepegawaian</span></div>
                            <t t-foreach="d.kepegawaian" t-as="b" t-key="b_index">
                                <div class="kp-info"><span t-esc="b[0]"/><span t-esc="b[1]"/></div>
                            </t>
                        </div>
                    </div>
                    <div class="kp-kartu">
                        <div class="kp-kartu-judul"><span><i class="fa fa-university"/> Rekening Pencairan</span></div>
                        <t t-foreach="d.rekening" t-as="b" t-key="b_index">
                            <div class="kp-info"><span t-esc="b[0]"/><span t-esc="b[1]"/></div>
                        </t>
                        <div class="kp-kecil" style="margin-top:10px">
                            Bila ada data yang keliru, hubungi admin di tempat kerja Anda.
                        </div>
                    </div>

                    <div class="kp-kartu">
                        <div class="kp-kartu-judul"><span><i class="fa fa-lock"/> Keamanan</span></div>
                        <button t-if="!state.formPassword" class="kp-tombol kp-tombol--garis" t-on-click="() => this.state.formPassword = true">
                            <i class="fa fa-key"/> Ganti Password
                        </button>
                        <div t-else="">
                            <div class="kp-isian">
                                <label>Password Lama</label>
                                <input class="kp-input" type="password" autocomplete="current-password"
                                       t-att-value="state.lama" t-on-input="(ev) => this.state.lama = ev.target.value"/>
                            </div>
                            <div class="kp-isian">
                                <label>Password Baru</label>
                                <input class="kp-input" type="password" autocomplete="new-password"
                                       t-att-value="state.baru" t-on-input="(ev) => this.state.baru = ev.target.value"/>
                                <ul class="kp-syarat">
                                    <li t-att-class="syarat.panjang ? 'ok' : ''">Minimal 8 karakter</li>
                                    <li t-att-class="syarat.campur ? 'ok' : ''">Memuat huruf dan angka</li>
                                </ul>
                            </div>
                            <div class="kp-isian">
                                <label>Ulangi Password Baru</label>
                                <input class="kp-input" type="password" autocomplete="new-password"
                                       t-att-value="state.ulang" t-on-input="(ev) => this.state.ulang = ev.target.value"/>
                                <div t-if="state.ulang and state.ulang !== state.baru" class="kp-galat">Password tidak sama.</div>
                            </div>
                            <div t-if="state.galatPassword" class="kp-peringatan kp-peringatan--merah" style="margin-bottom:12px">
                                <i class="fa fa-exclamation-circle"/><span t-esc="state.galatPassword"/>
                            </div>
                            <div class="kp-dua">
                                <button class="kp-tombol kp-tombol--garis" t-on-click="batalPassword">Batal</button>
                                <button class="kp-tombol" t-att-disabled="!bisaGanti" t-on-click="gantiPassword">
                                    <i t-if="state.simpan" class="fa fa-spinner fa-spin"/> Simpan
                                </button>
                            </div>
                        </div>
                    </div>

                    <div t-if="state.pwa !== 'terpasang'" class="kp-kartu">
                        <div class="kp-kartu-judul"><span><i class="fa fa-mobile"/> Pasang Aplikasi</span></div>
                        <t t-if="state.pwa === 'siap'">
                            <div class="kp-kecil" style="margin-bottom:10px">
                                Pasang portal ini di perangkat Anda supaya bisa dibuka langsung dari layar utama, seperti aplikasi biasa.
                            </div>
                            <button class="kp-tombol" t-on-click="pasang"><i class="fa fa-download"/> Pasang di Perangkat Ini</button>
                        </t>
                        <div t-elif="state.pwa === 'ios'" class="kp-kecil">
                            Di iPhone atau iPad: buka halaman ini dengan Safari, ketuk tombol <b>Bagikan</b>
                            <i class="fa fa-share-square-o"/>, lalu pilih <b>Tambah ke Layar Utama</b>.
                        </div>
                        <div t-elif="state.pwa === 'tidakaman'" class="kp-kecil">
                            Pemasangan sebagai aplikasi hanya tersedia bila portal dibuka lewat alamat aman (https).
                            Anda tetap bisa menambahkan halaman ini ke layar utama dari menu peramban.
                        </div>
                        <div t-else="" class="kp-kecil">
                            Buka menu peramban (<i class="fa fa-ellipsis-v"/>), lalu pilih <b>Instal aplikasi</b> atau
                            <b>Tambahkan ke layar utama</b>.
                        </div>
                    </div>

                    <a class="kp-tombol kp-tombol--merah" href="/koperasi/portal/logout"><i class="fa fa-sign-out"/> Keluar</a>
                    <div class="kp-kaki" t-esc="props.perusahaan"/>
                </div>
            </div>`;

        siapkan() {
            Object.assign(this.state, {
                unggah: false, formPassword: false, lama: '', baru: '', ulang: '', galatPassword: null, simpan: false,
                pwa: this.keadaanPwa(),
            });
            const pwa = window.KopPwa;
            if (pwa) {
                // tawaran pasang dari peramban bisa datang belakangan
                const berhenti = pwa.pantau(() => { this.state.pwa = this.keadaanPwa(); });
                onWillUnmount(berhenti);
            }
        }

        /** 'terpasang' | 'siap' (peramban menawarkan pasang) | 'ios' | 'tidakaman' | 'manual' */
        keadaanPwa() {
            const pwa = window.KopPwa;
            if (!pwa) return 'manual';
            if (pwa.terpasang) return 'terpasang';
            if (pwa.tawaran) return 'siap';
            if (pwa.ios) return 'ios';
            if (!pwa.aman) return 'tidakaman';
            return 'manual';
        }

        async pasang() {
            const hasil = await window.KopPwa.pasang();
            if (hasil === 'accepted') this.props.toast('Aplikasi sedang dipasang.', 'sukses');
            this.state.pwa = this.keadaanPwa();
        }

        get syarat() { return syaratPassword(this.state.baru); }

        get bisaGanti() {
            const s = this.syarat;
            return !this.state.simpan && this.state.lama && s.panjang && s.campur && this.state.baru === this.state.ulang;
        }

        batalPassword() {
            Object.assign(this.state, { formPassword: false, lama: '', baru: '', ulang: '', galatPassword: null });
        }

        async gantiPassword() {
            if (!this.bisaGanti) return;
            this.state.simpan = true;
            this.state.galatPassword = null;
            try {
                await this.panggil('/koperasi/portal/api/password', { lama: this.state.lama, baru: this.state.baru });
                this.batalPassword();
                this.props.toast('Password berhasil diganti.', 'sukses');
            } catch (e) {
                this.state.galatPassword = e.message;
            }
            this.state.simpan = false;
        }

        async gantiFoto(ev) {
            const berkas = ev.target.files && ev.target.files[0];
            ev.target.value = '';
            if (!berkas) return;
            this.state.unggah = true;
            try {
                const foto = await kecilkanFoto(berkas, 512);
                const anggota = await this.panggil('/koperasi/portal/api/foto', { foto });
                this.props.perbaruiAnggota(anggota);
                this.state.data.anggota = anggota;
                this.props.toast('Foto profil diperbarui.', 'sukses');
            } catch (e) {
                this.props.toast(e.message, 'galat');
            }
            this.state.unggah = false;
        }
    }

    // ── Kerangka aplikasi ────────────────────────────────────────────────────
    const TAB = [
        { id: 'beranda', label: 'Beranda', ikon: 'fa-home' },
        { id: 'simpanan', label: 'Simpanan', ikon: 'fa-bank' },
        { id: 'pinjaman', label: 'Pinjaman', ikon: 'fa-credit-card' },
        { id: 'profil', label: 'Profil', ikon: 'fa-user-circle' },
    ];

    class Aplikasi extends Component {
        static props = ['*'];
        static components = { Beranda, Simpanan, Pinjaman, Profil };
        static template = xml/* xml */`
            <div class="kp-shell">
                <Beranda t-if="state.tab === 'beranda'" anggota="state.anggota" pindah.bind="pindah"/>
                <Simpanan t-elif="state.tab === 'simpanan'" anggota="state.anggota"/>
                <Pinjaman t-elif="state.tab === 'pinjaman'" anggota="state.anggota"/>
                <Profil t-else="" anggota="state.anggota" perusahaan="props.perusahaan"
                        toast.bind="toast" perbaruiAnggota.bind="perbaruiAnggota"/>

                <nav class="kp-nav">
                    <t t-foreach="tabList" t-as="t" t-key="t.id">
                        <button t-att-class="state.tab === t.id ? 'aktif' : ''" t-on-click="() => this.pindah(t.id)">
                            <span class="kp-nav-ikon"><i t-attf-class="fa #{t.ikon}"/></span>
                            <span t-esc="t.label"/>
                        </button>
                    </t>
                </nav>
                <div t-if="state.toast" t-attf-class="kp-toast kp-toast--#{state.toast.jenis}" t-esc="state.toast.pesan"/>
            </div>`;

        setup() {
            this.tabList = TAB;
            const awal = (window.location.hash || '').replace('#', '');
            this.state = useState({
                tab: TAB.some((t) => t.id === awal) ? awal : 'beranda',
                anggota: this.props.data.anggota,
                toast: null,
            });
            // tombol kembali peramban berpindah antar halaman, bukan keluar dari aplikasi
            window.addEventListener('hashchange', () => {
                const tab = (window.location.hash || '').replace('#', '') || 'beranda';
                if (TAB.some((t) => t.id === tab)) this.state.tab = tab;
            });
        }

        pindah(tab) {
            if (tab === this.state.tab) return;
            window.location.hash = tab === 'beranda' ? '' : tab;
            this.state.tab = tab;
            window.scrollTo(0, 0);
        }

        toast(pesan, jenis) {
            this.state.toast = { pesan, jenis: jenis || 'info' };
            clearTimeout(this._waktuToast);
            this._waktuToast = setTimeout(() => { this.state.toast = null; }, 3200);
        }

        perbaruiAnggota(anggota) { this.state.anggota = anggota; }
    }

    function mulai() {
        const akar = document.getElementById('kp-app');
        if (!akar) return;
        mount(Aplikasi, akar, { props: { data: JSON.parse(akar.dataset.init), perusahaan: akar.dataset.perusahaan } });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mulai);
    else mulai();
})();
