/* Koperasi — Portal Anggota: halaman pendaftaran dari tautan undangan (OWL). */
(function () {
    'use strict';

    const { Component, useState, mount, xml } = owl;
    const { rpc, inisial, kecilkanFoto, syaratPassword } = window.KopPortal;

    class Daftar extends Component {
        static props = ['*'];
        static template = xml/* xml */`
            <div class="kp-shell kp-shell--polos">
                <div class="kp-hero">
                    <div class="kp-hero-merek">
                        <div class="kp-hero-logo"><i class="fa fa-users"/></div>
                        <div class="kp-hero-judul">Portal Anggota</div>
                        <div class="kp-hero-sub" t-esc="props.perusahaan"/>
                    </div>
                </div>

                <div class="kp-kartu kp-kartu--angkat kp-form-kartu">
                    <div class="kp-form-judul" t-esc="data.atur_ulang ? 'Atur Ulang Password' : 'Buat Akun Anda'"/>
                    <div class="kp-form-sub">
                        <t t-if="data.atur_ulang">Buat password baru untuk masuk ke portal.</t>
                        <t t-else="">Lengkapi username, password, dan foto profil untuk mulai memakai portal.</t>
                    </div>

                    <div class="kp-identitas">
                        <div class="kp-avatar kp-avatar--gelap"><t t-esc="inisialNama"/></div>
                        <div style="min-width:0">
                            <b t-esc="data.nama"/>
                            <div class="kp-kecil">
                                <t t-if="data.nomor"><t t-esc="data.nomor"/> · </t><t t-esc="data.mitra"/>
                            </div>
                        </div>
                    </div>

                    <!-- Foto profil -->
                    <div class="kp-foto">
                        <div class="kp-foto-lingkar">
                            <div class="kp-avatar kp-avatar--besar kp-avatar--gelap">
                                <img t-if="state.foto" t-att-src="state.foto" alt="Foto profil"/>
                                <i t-else="" class="fa fa-user"/>
                            </div>
                            <label class="kp-foto-tombol" title="Pilih foto">
                                <i class="fa fa-camera"/>
                                <input type="file" accept="image/*" t-on-change="pilihFoto"/>
                            </label>
                        </div>
                        <div class="kp-kecil">
                            <t t-if="state.foto">Foto siap dipakai · <a class="kp-tautan" href="#" t-on-click.prevent="hapusFoto">hapus</a></t>
                            <t t-elif="data.punya_foto">Foto profil sudah ada. Ketuk kamera untuk menggantinya.</t>
                            <t t-else="">Foto profil (opsional)</t>
                        </div>
                    </div>

                    <div class="kp-isian">
                        <label>Username</label>
                        <input class="kp-input" type="text" autocomplete="username" autocapitalize="none" spellcheck="false"
                               placeholder="mis. budi.santoso" t-att-readonly="data.atur_ulang"
                               t-att-value="state.username" t-on-input="isiUsername"/>
                        <div t-if="!data.atur_ulang" class="kp-bantuan">
                            4–32 karakter: huruf kecil, angka, titik, garis bawah, atau tanda hubung.
                        </div>
                    </div>

                    <div class="kp-isian">
                        <label>Password</label>
                        <div class="kp-input-bungkus">
                            <input class="kp-input kp-input--ikon" autocomplete="new-password"
                                   t-att-type="state.lihat ? 'text' : 'password'"
                                   t-att-value="state.password" t-on-input="(ev) => this.state.password = ev.target.value"/>
                            <button type="button" class="kp-input-aksi" t-on-click="() => this.state.lihat = !this.state.lihat"
                                    t-att-title="state.lihat ? 'Sembunyikan' : 'Tampilkan'">
                                <i t-attf-class="fa #{ state.lihat ? 'fa-eye-slash' : 'fa-eye' }"/>
                            </button>
                        </div>
                        <ul class="kp-syarat">
                            <li t-att-class="syarat.panjang ? 'ok' : ''">Minimal 8 karakter</li>
                            <li t-att-class="syarat.campur ? 'ok' : ''">Memuat huruf dan angka</li>
                        </ul>
                    </div>

                    <div class="kp-isian">
                        <label>Ulangi Password</label>
                        <input class="kp-input" autocomplete="new-password"
                               t-att-type="state.lihat ? 'text' : 'password'"
                               t-att-value="state.ulang" t-on-input="(ev) => this.state.ulang = ev.target.value"
                               t-on-keydown="tekan"/>
                        <div t-if="state.ulang and state.ulang !== state.password" class="kp-galat">Password tidak sama.</div>
                    </div>

                    <div t-if="state.galat" class="kp-peringatan kp-peringatan--merah" style="margin-bottom:14px">
                        <i class="fa fa-exclamation-circle"/><span t-esc="state.galat"/>
                    </div>

                    <button class="kp-tombol" t-att-disabled="!bisaKirim" t-on-click="kirim">
                        <i t-if="state.kirim" class="fa fa-spinner fa-spin"/>
                        <t t-esc="state.kirim ? 'Menyimpan...' : (data.atur_ulang ? 'Simpan Password' : 'Buat Akun')"/>
                    </button>
                </div>
                <div class="kp-kaki" style="margin-top:18px">
                    Sudah punya akun? <a class="kp-tautan" href="/koperasi/portal/login">Masuk</a>
                </div>
            </div>
        `;

        setup() {
            this.data = this.props.data;
            this.state = useState({
                username: this.data.username || '',
                password: '', ulang: '', lihat: false,
                foto: null, galat: null, kirim: false,
            });
        }

        get inisialNama() { return inisial(this.data.nama); }
        get syarat() { return syaratPassword(this.state.password); }

        get usernameSah() {
            return this.data.atur_ulang || /^[a-z0-9][a-z0-9._-]{3,31}$/.test(this.state.username);
        }

        get bisaKirim() {
            const s = this.syarat;
            return !this.state.kirim && this.usernameSah && s.panjang && s.campur
                && this.state.password === this.state.ulang;
        }

        isiUsername(ev) {
            // langsung dirapikan supaya sama dengan yang disimpan server
            const rapi = ev.target.value.toLowerCase().replace(/[^a-z0-9._-]/g, '');
            ev.target.value = rapi;
            this.state.username = rapi;
        }

        async pilihFoto(ev) {
            const berkas = ev.target.files && ev.target.files[0];
            ev.target.value = '';
            if (!berkas) return;
            try {
                this.state.foto = await kecilkanFoto(berkas, 512);
                this.state.galat = null;
            } catch (e) {
                this.state.galat = e.message;
            }
        }

        hapusFoto() { this.state.foto = null; }

        tekan(ev) {
            if (ev.key === 'Enter' && this.bisaKirim) this.kirim();
        }

        async kirim() {
            if (!this.bisaKirim) return;
            this.state.kirim = true;
            this.state.galat = null;
            try {
                const hasil = await rpc('/koperasi/daftar/submit', {
                    token: this.data.token, username: this.state.username,
                    password: this.state.password, foto: this.state.foto,
                });
                if (hasil.error) {
                    this.state.galat = hasil.error;
                    this.state.kirim = false;
                    return;
                }
                window.location.href = hasil.redirect || '/koperasi/portal';
            } catch (e) {
                this.state.galat = e.message;
                this.state.kirim = false;
            }
        }
    }

    function mulai() {
        const akar = document.getElementById('kp-daftar');
        if (!akar) return;
        mount(Daftar, akar, { props: { data: JSON.parse(akar.dataset.init), perusahaan: akar.dataset.perusahaan } });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mulai);
    else mulai();
})();
