/* Koperasi — Portal Anggota: fungsi bersama halaman daftar dan aplikasi.
   Berkas biasa (bukan modul Odoo): dimuat langsung oleh halaman portal setelah owl.js. */
(function () {
    'use strict';

    const BULAN = ['Jan', 'Feb', 'Mar', 'Apr', 'Mei', 'Jun', 'Jul', 'Agu', 'Sep', 'Okt', 'Nov', 'Des'];
    const angka = new Intl.NumberFormat('id-ID', { maximumFractionDigits: 0 });

    /** Panggil rute JSON-RPC Odoo. Melempar Error berisi pesan bila gagal. */
    async function rpc(url, params) {
        let respons;
        try {
            respons = await fetch(url, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'same-origin',
                body: JSON.stringify({ jsonrpc: '2.0', method: 'call', id: Date.now(), params: params || {} }),
            });
        } catch (e) {
            throw new Error('Tidak dapat terhubung ke server. Periksa koneksi internet Anda.');
        }
        if (!respons.ok) throw new Error('Server sedang bermasalah (' + respons.status + '). Coba lagi.');
        const json = await respons.json();
        if (json.error) {
            const data = json.error.data || {};
            throw new Error(data.message || json.error.message || 'Terjadi kesalahan.');
        }
        return json.result;
    }

    function rp(nilai) {
        const n = Math.round(Number(nilai) || 0);
        return (n < 0 ? '-Rp ' : 'Rp ') + angka.format(Math.abs(n));
    }

    /** '2026-10-08' -> '8 Okt 2026' */
    function tgl(teks) {
        if (!teks) return '';
        const [t, b, h] = String(teks).split('-');
        if (!h) return String(teks);
        return parseInt(h, 10) + ' ' + BULAN[parseInt(b, 10) - 1] + ' ' + t;
    }

    function inisial(nama) {
        if (!nama) return '?';
        return nama.trim().split(/\s+/).slice(0, 2).map((k) => k[0]).join('').toUpperCase();
    }

    /** Kelas warna lencana dari kode atau teks status. */
    function warnaStatus(status) {
        const s = String(status || '').toLowerCase();
        if (s.startsWith('belum dibayar') || ['belum', 'ditolak', 'macet', 'diragukan', 'keluar'].includes(s)) return 'merah';
        if (['dibayar', 'lunas', 'aktif', 'lancar'].includes(s)) return 'hijau';
        if (['berjalan', 'ditagihkan'].includes(s)) return 'biru';
        if (['diajukan', 'disetujui', 'menunggu', 'perhatian', 'kurang', 'ditarik'].includes(s)
            || s.startsWith('menunggu') || s.startsWith('belum divalidasi')) return 'jingga';
        return 'abu';
    }

    /** Kecilkan gambar pilihan pengguna menjadi JPEG persegi (dipotong tengah) -> data URL. */
    function kecilkanFoto(berkas, sisi) {
        sisi = sisi || 512;
        return new Promise((selesai, gagal) => {
            if (!berkas || !/^image\//.test(berkas.type)) return gagal(new Error('Pilih berkas gambar.'));
            const pembaca = new FileReader();
            pembaca.onerror = () => gagal(new Error('Foto tidak dapat dibaca.'));
            pembaca.onload = () => {
                const gambar = new Image();
                gambar.onerror = () => gagal(new Error('Foto tidak dapat dibaca.'));
                gambar.onload = () => {
                    const pendek = Math.min(gambar.width, gambar.height);
                    const kanvas = document.createElement('canvas');
                    kanvas.width = kanvas.height = Math.min(sisi, pendek);
                    kanvas.getContext('2d').drawImage(
                        gambar, (gambar.width - pendek) / 2, (gambar.height - pendek) / 2, pendek, pendek,
                        0, 0, kanvas.width, kanvas.height);
                    selesai(kanvas.toDataURL('image/jpeg', 0.85));
                };
                gambar.src = pembaca.result;
            };
            pembaca.readAsDataURL(berkas);
        });
    }

    /** Syarat password; sama dengan pemeriksaan di server. */
    function syaratPassword(p) {
        p = p || '';
        return {
            panjang: p.length >= 8,
            campur: /[A-Za-z]/.test(p) && /[0-9]/.test(p),
        };
    }

    window.KopPortal = { rpc, rp, tgl, inisial, warnaStatus, kecilkanFoto, syaratPassword };
})();
