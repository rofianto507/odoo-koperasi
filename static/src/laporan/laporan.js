/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const angka = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 });
const angka1 = new Intl.NumberFormat("id-ID", { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Warna lencana dari teks status (yang diawali "Belum" dicek lebih dulu). */
function kelasStatusTeks(teks) {
    const t = (teks || "").toLowerCase();
    if (t.startsWith("belum") || t === "ditolak") return "merah";
    if (t === "dibayar" || t === "lunas" || t === "selesai") return "hijau";
    if (t === "berjalan" || t === "ditagihkan") return "biru";
    return "abu";
}

export class KoperasiLaporan extends Component {
    static template = "koperasi.Laporan";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");

        this.state = useState({
            loading: true,
            galat: null,
            opsi: null,
            jenis: "ringkasan",
            mitraId: false,
            dari: false,
            sampai: false,
            anggotaId: false,
            anggotaList: [],
            laporan: null,
            sidebarCollapsed: false,
        });

        onWillStart(async () => {
            const opsi = await this.orm.call("koperasi.laporan", "get_opsi", [], {});
            this.state.opsi = opsi;
            this.state.dari = opsi.dari;
            this.state.sampai = opsi.sampai;
            await this.muat();
        });
    }

    // ── Data ────────────────────────────────────────────────────────────────

    async muat() {
        this.state.loading = true;
        this.state.galat = null;
        try {
            if (this.state.jenis === "anggota") {
                this.state.anggotaList = await this.orm.call(
                    "koperasi.laporan", "daftar_anggota", [], { mitra_id: this.state.mitraId || false });
                if (!this.state.anggotaList.some((a) => a.id === this.state.anggotaId)) {
                    this.state.anggotaId = false;
                }
            }
            this.state.laporan = await this.orm.call("koperasi.laporan", "get_laporan", [], {
                jenis: this.state.jenis,
                mitra_id: this.state.mitraId || false,
                dari: this.state.dari || false,
                sampai: this.state.sampai || false,
                anggota_id: this.state.anggotaId || false,
            });
        } catch (e) {
            this.state.galat = (e && e.data && e.data.message) || (e && e.message) || "Gagal memuat laporan.";
        }
        this.state.loading = false;
    }

    setJenis(id) {
        this.state.jenis = id;
        this.muat();
    }

    onMitra(ev) {
        this.state.mitraId = parseInt(ev.target.value, 10) || false;
        this.muat();
    }

    /** Urutan periode (0 = paling awal); daftar dari server sudah menaik menurut tanggal. */
    urutPeriode(id) {
        return this.state.opsi.periode_list.findIndex((p) => p.id === id);
    }

    /** Apakah periode `awal` jatuh setelah periode `akhir` (rentang akan terbalik). */
    periodeLewat(awal, akhir) {
        return this.urutPeriode(awal) > this.urutPeriode(akhir);
    }

    // Pilihan yang membuat rentang terbalik sudah dinonaktifkan di daftar; dua pengaman di bawah
    // menjaga bila nilainya tetap masuk (mis. lewat keyboard di sebagian peramban).
    onDari(ev) {
        const id = parseInt(ev.target.value, 10) || false;
        this.state.dari = id;
        if (this.periodeLewat(id, this.state.sampai)) {
            this.state.sampai = id;
        }
        this.muat();
    }

    onSampai(ev) {
        const id = parseInt(ev.target.value, 10) || false;
        this.state.sampai = id;
        if (this.periodeLewat(this.state.dari, id)) {
            this.state.dari = id;
        }
        this.muat();
    }

    onAnggota(ev) {
        this.state.anggotaId = parseInt(ev.target.value, 10) || false;
        this.muat();
    }

    toggleSidebar() {
        this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
    }

    get jenisAktif() {
        return this.state.opsi.jenis.find((j) => j.id === this.state.jenis);
    }

    // ── Unduhan ─────────────────────────────────────────────────────────────

    unduh(format) {
        const s = this.state;
        const params = new URLSearchParams({
            jenis: s.jenis, mitra: s.mitraId || 0, dari: s.dari || 0, sampai: s.sampai || 0, anggota: s.anggotaId || 0,
        });
        this.action.doAction({
            type: "ir.actions.act_url",
            url: `/koperasi/laporan/${format}?${params.toString()}`,
            target: format === "pdf" ? "new" : "self",
        });
    }

    get bisaUnduh() {
        const l = this.state.laporan;
        return !!l && !l.perlu_anggota && !this.state.loading;
    }

    // ── Format tampilan ─────────────────────────────────────────────────────

    fmt(nilai, tipe) {
        if (nilai === null || nilai === undefined || nilai === "") return "";
        if (tipe === "rp") {
            const teks = angka.format(Math.abs(Math.round(nilai)));
            return (nilai < 0 ? "-Rp " : "Rp ") + teks;
        }
        if (tipe === "angka") return angka.format(nilai);
        if (tipe === "persen") return angka1.format(nilai) + "%";
        if (tipe === "tanggal") {
            const [t, b, h] = String(nilai).split("-");
            return h ? `${h}/${b}/${t}` : String(nilai);
        }
        return String(nilai);
    }

    /** Nilai kartu ringkasan: teks bila tidak ada angka. */
    nilaiKartu(k) {
        return k.nilai === null ? k.sub : this.fmt(k.nilai, k.tipe);
    }

    kelasSel(k) {
        if (["rp", "angka", "persen"].includes(k.tipe)) return "kop-num";
        if (k.tipe === "tanggal") return "kop-tgh";
        return "";
    }

    kelasBaris(r) {
        return r._gaya ? "kop-lap-baris-" + r._gaya : "";
    }

    kelasStatus(teks) {
        return "kop-badge kop-badge--" + kelasStatusTeks(teks);
    }

    /** Isi sel pada baris total: jumlah untuk kolom ber-jumlah, label "TOTAL" pada kolom pertama. */
    selTotal(b, k, indeks) {
        if (k.jumlah) return this.fmt(b.total[k.key], k.tipe);
        return indeks === 0 ? b.total[k.key] : "";
    }

    tampilTotal(b) {
        return !!b.total && b.baris.length > 0;
    }
}

registry.category("actions").add("koperasi.laporan", KoperasiLaporan);
