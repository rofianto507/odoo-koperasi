/* Koperasi — Portal Anggota: dukungan pemasangan sebagai aplikasi (PWA).
   Dimuat di semua halaman portal. Mendaftarkan service worker dan menampung tawaran pasang
   dari peramban supaya bisa dipicu dari tombol di halaman Profil. */
(function () {
    'use strict';

    const ua = window.navigator.userAgent || '';
    const pendengar = [];
    const P = window.KopPwa = {
        tawaran: null,                       // event beforeinstallprompt (Chrome/Edge/Android)
        ios: /iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && 'ontouchend' in document),
        aman: !!window.isSecureContext,      // pemasangan butuh https (atau localhost)
        get terpasang() {
            return (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches)
                || window.navigator.standalone === true;
        },
        /** Panggil fn tiap keadaan pemasangan berubah; mengembalikan fungsi untuk berhenti. */
        pantau(fn) {
            pendengar.push(fn);
            return () => { const i = pendengar.indexOf(fn); if (i >= 0) pendengar.splice(i, 1); };
        },
        /** Tampilkan dialog pasang milik peramban. Hasil: 'accepted', 'dismissed', atau null. */
        async pasang() {
            if (!P.tawaran) return null;
            const tawaran = P.tawaran;
            P.tawaran = null;
            tawaran.prompt();
            const pilihan = await tawaran.userChoice;
            kabari();
            return pilihan && pilihan.outcome;
        },
    };

    function kabari() { pendengar.slice().forEach((fn) => fn()); }

    window.addEventListener('beforeinstallprompt', (ev) => {
        ev.preventDefault();  // jangan tampilkan spanduk bawaan; kita tawarkan lewat tombol sendiri
        P.tawaran = ev;
        kabari();
    });
    window.addEventListener('appinstalled', () => { P.tawaran = null; kabari(); });

    if ('serviceWorker' in window.navigator) {
        window.addEventListener('load', () => {
            window.navigator.serviceWorker
                .register('/koperasi/portal/sw.js', { scope: '/koperasi/portal' })
                .catch(() => {});  // tanpa service worker portal tetap berjalan seperti biasa
        });
    }
})();
