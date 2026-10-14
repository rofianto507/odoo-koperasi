/** @odoo-module **/
/* global echarts */

import { Component, useState, onWillStart, onMounted, onWillUnmount, useRef, useEffect } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { loadJS } from "@web/core/assets";

const WARNA = {
    ungu: "#7c3aed", biru: "#3b82f6", hijau: "#10b981", jingga: "#f59e0b",
    merah: "#ef4444", abu: "#94a3b8", toska: "#0d9488",
};
const PALET = [WARNA.ungu, WARNA.biru, WARNA.hijau, WARNA.jingga, WARNA.merah, WARNA.toska, WARNA.abu];

const BAGIAN = [
    { id: "ringkasan", label: "Ringkasan", ikon: "fa-tachometer" },
    { id: "pinjaman", label: "Pinjaman", ikon: "fa-credit-card" },
    { id: "tagihan", label: "Tagihan", ikon: "fa-file-text-o" },
];

// Warna lencana status (pinjaman, tagihan, status bayar)
const KELAS_STATUS = {
    draft: "abu", diajukan: "jingga", disetujui: "biru", berjalan: "biru", lunas: "hijau",
    ditolak: "merah", terbit: "jingga", menunggu: "abu", dibayar: "hijau", belum: "merah",
};

const angka = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 0 });
const angka1 = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 1 });

/** Rp 1.234.567 */
function rp(n) {
    return "Rp " + angka.format(Math.round(n || 0));
}

/** Ringkas untuk kartu dan sumbu grafik: Rp 1,2 jt / Rp 3,4 M */
function rpRingkas(n) {
    const v = Math.abs(n || 0);
    const tanda = (n || 0) < 0 ? "-" : "";
    if (v >= 1e12) return `${tanda}Rp ${angka1.format(v / 1e12)} T`;
    if (v >= 1e9) return `${tanda}Rp ${angka1.format(v / 1e9)} M`;
    if (v >= 1e6) return `${tanda}Rp ${angka1.format(v / 1e6)} jt`;
    if (v >= 1e4) return `${tanda}Rp ${angka1.format(v / 1e3)} rb`;
    return `${tanda}Rp ${angka.format(v)}`;
}

const TEKS_SUMBU = { fontSize: 11, color: "#6b7280" };
const TOOLTIP = {
    backgroundColor: "#fff", borderColor: "#e5e7eb", borderWidth: 1,
    textStyle: { color: "#374151", fontSize: 12 },
};

export class KoperasiDashboard extends Component {
    static template = "koperasi.Dashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.bagian = BAGIAN;
        this.rp = rp;
        this.rpRingkas = rpRingkas;
        this.angka = (n) => angka.format(n || 0);

        this.state = useState({
            section: "ringkasan",
            sidebarCollapsed: false,
            loading: true,
            galat: null,
            mitraId: false,
            // 'sekarang' (bulan berjalan, bawaan) | 'semua' | id koperasi.periode
            periode: "sekarang",
            data: null,
        });

        this.refs = {
            utama: useRef("chartUtama"),
            samping: useRef("chartSamping"),
        };
        this.charts = {};
        this._onResize = () => Object.values(this.charts).forEach((c) => c && c.resize());

        onWillStart(async () => {
            await loadJS("/koperasi/static/lib/echarts/echarts.min.js");
            await this.loadData();
        });

        // Grafik digambar ulang tiap data atau bagian berganti (elemen <div>-nya ikut berganti).
        useEffect(
            () => {
                if (!this.state.loading && this.state.data) {
                    this.renderCharts();
                }
            },
            () => [this.state.loading, this.state.section, this.state.data]
        );

        onMounted(() => window.addEventListener("resize", this._onResize));
        onWillUnmount(() => {
            window.removeEventListener("resize", this._onResize);
            this._buangChart();
        });
    }

    // ── Data ────────────────────────────────────────────────────────────────

    async loadData() {
        this.state.loading = true;
        this.state.galat = null;
        try {
            this.state.data = await this.orm.call("koperasi.dashboard", "get_data", [], {
                mitra_id: this.state.mitraId || false,
                periode: this.state.periode,
            });
        } catch (e) {
            this.state.galat = (e && e.data && e.data.message) || (e && e.message) || "Gagal memuat data.";
        }
        this.state.loading = false;
    }

    onMitraChange(ev) {
        this.state.mitraId = parseInt(ev.target.value, 10) || false;
        this.loadData();
    }

    onPeriodeChange(ev) {
        const v = ev.target.value;
        this.state.periode = v === "semua" ? "semua" : parseInt(v, 10);
        this.loadData();
    }

    /**
     * Apakah pilihan ini (id periode, atau false untuk "Semua Periode") yang sedang
     * dipakai server. Dibandingkan di sini karena template OWL tidak mengenal fungsi
     * global seperti String().
     */
    periodeDipilih(id) {
        const d = this.state.data;
        return ((d && d.periode.id) || false) === (id || false);
    }

    /** Label periode untuk teks kartu: "OKT 2026" atau "semua periode". */
    get labelPeriode() {
        const d = this.state.data;
        return d && d.periode.id ? d.periode.label : "semua periode";
    }

    get domainPeriode() {
        const d = this.state.data;
        return d && d.periode.id ? [["periode_id", "=", d.periode.id]] : [];
    }

    setSection(id) {
        this.state.section = id;
    }

    toggleSidebar() {
        this.state.sidebarCollapsed = !this.state.sidebarCollapsed;
        // lebar konten berubah setelah animasi sidebar selesai
        setTimeout(this._onResize, 260);
    }

    get bagianAktif() {
        return BAGIAN.find((b) => b.id === this.state.section);
    }

    get namaMitra() {
        const d = this.state.data;
        const m = d && d.mitra_list.find((x) => x.id === this.state.mitraId);
        return m ? m.name : "Semua Mitra";
    }

    persen(bagian, total) {
        return total ? Math.max(2, Math.min(100, Math.round((bagian * 100) / total))) : 0;
    }

    kelasStatus(state) {
        return "kop-badge kop-badge--" + (KELAS_STATUS[state] || "abu");
    }

    // ── Navigasi ke data ────────────────────────────────────────────────────

    get domainMitra() {
        return this.state.mitraId ? [["mitra_id", "=", this.state.mitraId]] : [];
    }

    buka(model, nama, domain = [], mode = "list") {
        const views = mode === "list" ? [[false, "list"], [false, "form"]] : [[false, mode]];
        this.action.doAction({
            type: "ir.actions.act_window",
            name: nama,
            res_model: model,
            views,
            domain: [...this.domainMitra, ...domain],
        });
    }

    bukaRecord(model, id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: model,
            res_id: id,
            views: [[false, "form"]],
        });
    }

    bukaTunggakan() {
        this.action.doAction({
            type: "ir.actions.act_window",
            name: "Tunggakan",
            res_model: "koperasi.tagihan.line",
            views: [[false, "list"]],
            domain: [
                ...this.domainMitra,
                "|", "&", ["status", "=", "belum"], ["dibawa_ke_id", "=", false],
                "&", ["status", "=", "menunggu"], ["tunggakan", ">", 0],
            ],
        });
    }

    // ── Isi tiap bagian (kartu, antrean, tabel) ─────────────────────────────

    /** Empat kartu besar di atas tiap bagian. */
    get kartu() {
        const d = this.state.data;
        if (!d) return [];
        const r = d.ringkasan, p = d.pinjaman, t = d.tagihan;
        // lp = label periode terpilih; kartu "posisi" memakai chip "Saat ini"
        const lp = this.labelPeriode;
        const adaP = !!d.periode.id;
        const bukaAnggota = () => this.buka("koperasi.anggota", "Anggota Aktif", [["state", "=", "aktif"]]);
        const bukaBerjalan = () => this.buka("koperasi.pinjaman", "Pinjaman Berjalan", [["state", "=", "berjalan"]]);
        const bukaTagihan = (extra = []) =>
            this.buka("koperasi.tagihan", "Tagihan", [...this.domainPeriode, ...extra]);
        const tunggakan = (x) => ({
            warna: x ? "merah" : "abu", ikon: "fa-exclamation-triangle",
            chip: x ? "Saat ini · perlu ditagih" : "Saat ini · tidak ada", nilai: rpRingkas(x),
            label: "Tunggakan", sub: rp(x), aksi: () => this.bukaTunggakan(),
        });
        const semua = {
            ringkasan: [
                { warna: "ungu", ikon: "fa-users", chip: `${r.mitra} mitra`, nilai: this.angka(r.anggota_aktif),
                  label: "Anggota Aktif", sub: `dari ${this.angka(r.anggota_total)} terdaftar`,
                  bar: this.persen(r.anggota_aktif, r.anggota_total), aksi: bukaAnggota },
                { warna: "hijau", ikon: "fa-line-chart", chip: lp, nilai: rpRingkas(r.angsuran_diterima),
                  label: "Angsuran Diterima", sub: `${lp}`, aksi: () => bukaTagihan() },
                { warna: "biru", ikon: "fa-credit-card", chip: `${r.pinjaman_berjalan} pinjaman berjalan`,
                  nilai: rpRingkas(r.pinjaman_sisa), label: "Sisa Pinjaman",
                  sub: `${rp(r.pinjaman_cair)} dicairkan ${lp}`, aksi: bukaBerjalan },
                tunggakan(r.tunggakan),
            ],
            pinjaman: [
                { warna: "ungu", ikon: "fa-paper-plane", chip: lp, nilai: rpRingkas(p.tersalurkan),
                  label: "Dana Tersalurkan", sub: `${p.jumlah_cair} pinjaman dicairkan`,
                  aksi: () => this.buka("koperasi.pinjaman", "Pinjaman Cair", [["state", "in", ["berjalan", "lunas"]]]) },
                { warna: "biru", ikon: "fa-credit-card", chip: `Saat ini · ${p.berjalan} berjalan`,
                  nilai: rpRingkas(p.sisa), label: "Sisa Pokok",
                  sub: `dari ${rp(p.total_tersalurkan)} tersalurkan`,
                  bar: this.persen(p.total_tersalurkan - p.sisa, p.total_tersalurkan), aksi: bukaBerjalan },
                { warna: "hijau", ikon: "fa-line-chart", chip: lp, nilai: rpRingkas(p.pendapatan),
                  label: "Pendapatan Platform",
                  sub: `Jasa ${rpRingkas(p.jasa_diterima)} · Admin ${rpRingkas(p.admin_diterima)}`,
                  aksi: bukaBerjalan },
                { warna: "abu", ikon: "fa-check-circle", chip: lp, nilai: this.angka(p.lunas),
                  label: "Pinjaman Lunas", sub: adaP ? "lunas pada periode ini" : "sejak awal",
                  aksi: () => this.buka("koperasi.pinjaman", "Pinjaman Lunas", [["state", "=", "lunas"]]) },
            ],
            tagihan: [
                { warna: "ungu", ikon: "fa-file-text-o", chip: lp, nilai: rpRingkas(t.ditagihkan),
                  label: "Total Ditagihkan", sub: "Angsuran pokok + jasa", aksi: () => bukaTagihan() },
                { warna: "hijau", ikon: "fa-check", chip: lp, nilai: rpRingkas(t.diterima),
                  label: "Diterima", sub: rp(t.diterima), bar: this.persen(t.diterima, t.ditagihkan),
                  aksi: () => bukaTagihan() },
                { warna: "jingga", ikon: "fa-hourglass-half", chip: lp, nilai: rpRingkas(t.menunggu),
                  label: "Menunggu Validasi", sub: rp(t.menunggu), bar: this.persen(t.menunggu, t.ditagihkan),
                  aksi: () => bukaTagihan([["state", "=", "terbit"]]) },
                tunggakan(t.tunggakan),
            ],
        };
        return semua[this.state.section] || [];
    }

    /** Antrean pekerjaan: hanya di Ringkasan. */
    get antrean() {
        const d = this.state.data;
        if (!d || this.state.section !== "ringkasan") return [];
        const r = d.ringkasan;
        return [
            { ikon: "fa-user-plus", warna: "jingga", nilai: r.anggota_menunggu, label: "Anggota Menunggu Validasi",
              sub: `${r.anggota_draft} masih draft`,
              aksi: () => this.buka("koperasi.anggota", "Menunggu Validasi", [["state", "=", "menunggu"]]) },
            { ikon: "fa-file-text", warna: "ungu", nilai: r.pinjaman_diajukan, label: "Pinjaman Menunggu Persetujuan",
              sub: "Sudah divalidasi mitra",
              aksi: () => this.buka("koperasi.pinjaman", "Menunggu Persetujuan", [["state", "=", "diajukan"]]) },
            { ikon: "fa-paper-plane", warna: "biru", nilai: r.pinjaman_disetujui, label: "Pinjaman Menunggu Pencairan",
              sub: "Sudah disetujui",
              aksi: () => this.buka("koperasi.pinjaman", "Menunggu Pencairan", [["state", "=", "disetujui"]]) },
            { ikon: "fa-check-square-o", warna: "hijau", nilai: r.tagihan_terbit, label: "Tagihan Belum Selesai Divalidasi",
              sub: d.is_operator ? `${r.tagihan_draft} tagihan masih draft` : "Menunggu validasi pembayaran",
              aksi: () => this.buka("koperasi.tagihan", "Tagihan Ditagihkan", [["state", "=", "terbit"]]) },
        ];
    }

    /** Judul dua grafik pada bagian aktif. */
    get judulGrafik() {
        const lp = this.labelPeriode;
        return {
            ringkasan: [["Tagihan per Periode", "ditagihkan vs diterima"], ["Status Pinjaman", "jumlah pinjaman saat ini"]],
            pinjaman: [["Penyaluran Pinjaman per Bulan", "pokok yang dicairkan"], ["Status Pinjaman", "jumlah pinjaman saat ini"]],
            tagihan: [["Penerimaan per Periode", "diterima, belum dibayar, belum divalidasi"], ["Kolektibilitas", `tagihan ${lp} yang sudah divalidasi`]],
        }[this.state.section];
    }

    /** Dua tabel di bawah grafik. Tipe kolom: teks | kode | rp | angka | status. */
    get tabel() {
        const d = this.state.data;
        if (!d) return [];
        const kolomPinjaman = [
            { label: "Nomor", key: "name", tipe: "kode" },
            { label: "Anggota", key: "anggota" },
            { label: "Mitra", key: "mitra" },
        ];
        const semua = {
            ringkasan: [
                { judul: "Ringkasan per Mitra", ikon: "fa-building-o", lencana: `${d.per_mitra.length} mitra`,
                  kosong: "Belum ada mitra", baris: d.per_mitra, model: "koperasi.mitra",
                  kolom: [
                      { label: "Mitra", key: "name" },
                      { label: "Anggota", key: "anggota", tipe: "angka" },
                      { label: "Sisa Pinjaman", key: "pinjaman_sisa", tipe: "rp" },
                      { label: "Tunggakan", key: "tunggakan", tipe: "rp", merah: true },
                  ] },
                { judul: "Pengajuan Pinjaman Perlu Tindakan", ikon: "fa-inbox",
                  lencana: `${d.pinjaman.pengajuan.length} pengajuan`, lencanaWarna: "jingga",
                  kosong: "Tidak ada pengajuan yang menunggu", baris: d.pinjaman.pengajuan, model: "koperasi.pinjaman",
                  kolom: [...kolomPinjaman,
                      { label: "Pokok", key: "pokok", tipe: "rp" },
                      { label: "Status", key: "state", tipe: "status" }] },
            ],
            pinjaman: [
                { judul: "Pengajuan Perlu Tindakan", ikon: "fa-inbox",
                  lencana: `${d.pinjaman.pengajuan.length} pengajuan`, lencanaWarna: "jingga",
                  kosong: "Tidak ada pengajuan yang menunggu", baris: d.pinjaman.pengajuan, model: "koperasi.pinjaman",
                  kolom: [...kolomPinjaman,
                      { label: "Pokok", key: "pokok", tipe: "rp" },
                      { label: "Tenor", key: "tenor", tipe: "angka" },
                      { label: "Status", key: "state", tipe: "status" }] },
                { judul: "Pinjaman Berjalan Terbesar", ikon: "fa-sort-amount-desc", lencana: "menurut sisa pokok",
                  kosong: "Belum ada pinjaman berjalan", baris: d.pinjaman.terbesar, model: "koperasi.pinjaman",
                  kolom: [...kolomPinjaman,
                      { label: "Angsuran", key: "angsuran", tipe: "rp" },
                      { label: "Terbayar", key: "dibayar", tipe: "progres" },
                      { label: "Sisa Pokok", key: "sisa", tipe: "rp", tebal: true }] },
            ],
            tagihan: [
                { judul: "Tagihan", ikon: "fa-file-text-o", lencana: this.labelPeriode,
                  kosong: "Belum ada tagihan pada periode ini", baris: d.tagihan.terbaru, model: "koperasi.tagihan",
                  kolom: [
                      { label: "Nomor", key: "name", tipe: "kode" },
                      { label: "Mitra", key: "mitra" },
                      { label: "Periode", key: "periode" },
                      { label: "Total", key: "total", tipe: "rp" },
                      { label: "Dibayar", key: "dibayar", tipe: "rp" },
                      { label: "Status", key: "state", tipe: "status" },
                  ] },
                { judul: "Anggota Menunggak", ikon: "fa-exclamation-circle",
                  lencana: `${d.tagihan.menunggak.length} anggota`, lencanaWarna: "merah",
                  kosong: "Tidak ada anggota yang menunggak", kosongOk: true,
                  baris: d.tagihan.menunggak, model: "koperasi.anggota",
                  kolom: [
                      { label: "No. Anggota", key: "nomor", tipe: "kode" },
                      { label: "Anggota", key: "name" },
                      { label: "Mitra", key: "mitra" },
                      { label: "Tunggakan", key: "tunggakan", tipe: "rp", merah: true },
                  ] },
            ],
        };
        return semua[this.state.section] || [];
    }

    // ── Grafik ──────────────────────────────────────────────────────────────

    _buangChart() {
        Object.values(this.charts).forEach((c) => c && c.dispose());
        this.charts = {};
    }

    _chart(nama, option) {
        const el = this.refs[nama].el;
        if (!el || typeof echarts === "undefined") return;
        this.charts[nama] = echarts.init(el);
        this.charts[nama].setOption(option);
    }

    _kosong(teks) {
        return {
            graphic: [{ type: "text", left: "center", top: "middle",
                        style: { text: teks, fill: "#9ca3af", fontSize: 13 } }],
        };
    }

    _batang(label, seri, { tumpuk = true } = {}) {
        if (!label.length) return this._kosong("Belum ada data");
        return {
            tooltip: {
                ...TOOLTIP, trigger: "axis", axisPointer: { type: "shadow" },
                valueFormatter: (v) => rp(v),
            },
            legend: { bottom: 0, textStyle: TEKS_SUMBU, itemWidth: 12, itemHeight: 8 },
            grid: { left: 8, right: 16, top: 16, bottom: 36, containLabel: true },
            xAxis: {
                type: "category", data: label, axisLabel: TEKS_SUMBU,
                axisLine: { lineStyle: { color: "#e5e7eb" } }, axisTick: { show: false },
            },
            yAxis: {
                type: "value", axisLabel: { ...TEKS_SUMBU, formatter: (v) => rpRingkas(v).replace("Rp ", "") },
                splitLine: { lineStyle: { color: "#f3f4f6", type: "dashed" } },
            },
            series: seri.map((s) => s.garis ? {
                name: s.nama, type: "line", data: s.data, smooth: true, symbol: "circle", symbolSize: 7,
                lineStyle: { color: s.warna, width: 2.5 },
                itemStyle: { color: s.warna, borderColor: "#fff", borderWidth: 2 },
            } : {
                name: s.nama, type: "bar", stack: tumpuk ? "total" : undefined, data: s.data,
                barMaxWidth: 38, itemStyle: { color: s.warna, borderRadius: 2 },
            }),
        };
    }

    _donat(data, { uang = true } = {}) {
        const isi = data.filter((d) => d.value > 0);
        if (!isi.length) return this._kosong("Belum ada data");
        return {
            tooltip: {
                ...TOOLTIP, trigger: "item",
                formatter: (p) => `${p.name}<br/><b>${uang ? rp(p.value) : angka.format(p.value)}</b> (${p.percent}%)`,
            },
            legend: { bottom: 0, textStyle: TEKS_SUMBU, itemWidth: 12, itemHeight: 8 },
            series: [{
                type: "pie", radius: ["42%", "68%"], center: ["50%", "44%"],
                label: { show: false }, labelLine: { show: false },
                data: isi.map((d, i) => ({
                    value: d.value, name: d.name, itemStyle: { color: d.warna || PALET[i % PALET.length] },
                })),
            }],
        };
    }

    renderCharts() {
        this._buangChart();
        const d = this.state.data;
        const tren = d.tren;
        const periode = tren.map((t) => t.periode);
        const s = this.state.section;

        const warnaStatus = {
            draft: WARNA.abu, diajukan: WARNA.jingga, disetujui: WARNA.toska,
            berjalan: WARNA.biru, lunas: WARNA.hijau, ditolak: WARNA.merah,
        };
        if (s === "ringkasan") {
            this._chart("utama", this._batang(periode, [
                { nama: "Angsuran ditagihkan", warna: WARNA.biru, data: tren.map((t) => t.angsuran) },
                { nama: "Diterima", warna: WARNA.hijau, garis: true, data: tren.map((t) => t.diterima) },
            ]));
            this._chart("samping", this._donat(
                d.pinjaman.per_state.map((x) => ({ name: x.label, value: x.jumlah, warna: warnaStatus[x.state] })),
                { uang: false }));
        } else if (s === "pinjaman") {
            const salur = d.pinjaman.penyaluran;
            this._chart("utama", this._batang(salur.map((x) => x.label), [
                { nama: "Pokok dicairkan", warna: WARNA.ungu, data: salur.map((x) => x.pokok) },
            ]));
            this._chart("samping", this._donat(
                d.pinjaman.per_state.map((x) => ({ name: x.label, value: x.jumlah, warna: warnaStatus[x.state] })),
                { uang: false }));
        } else if (s === "tagihan") {
            this._chart("utama", this._batang(periode, [
                { nama: "Diterima", warna: WARNA.hijau, data: tren.map((t) => t.diterima) },
                { nama: "Belum dibayar", warna: WARNA.merah, data: tren.map((t) => t.belum) },
                { nama: "Belum divalidasi", warna: WARNA.abu,
                  data: tren.map((t) => Math.max(t.ditagihkan - t.diterima - t.belum, 0)) },
            ]));
            const k = d.tagihan.kolektibilitas;
            this._chart("samping", k === null ? this._kosong("Belum ada tagihan yang divalidasi") : {
                series: [{
                    type: "gauge", startAngle: 200, endAngle: -20, min: 0, max: 100,
                    center: ["50%", "60%"], radius: "92%",
                    progress: { show: true, width: 16, itemStyle: { color: k >= 90 ? WARNA.hijau : k >= 70 ? WARNA.jingga : WARNA.merah } },
                    axisLine: { lineStyle: { width: 16, color: [[1, "#f3f4f6"]] } },
                    axisTick: { show: false }, splitLine: { show: false },
                    axisLabel: { show: false }, pointer: { show: false }, anchor: { show: false },
                    title: { offsetCenter: [0, "32%"], fontSize: 12, color: "#6b7280" },
                    detail: {
                        offsetCenter: [0, "-4%"], fontSize: 34, fontWeight: 700, color: "#1f2937",
                        formatter: (v) => angka1.format(v) + "%",
                    },
                    data: [{ value: k, name: "tagihan dibayar" }],
                }],
            });
        }
    }
}

registry.category("actions").add("koperasi.dashboard", KoperasiDashboard);
