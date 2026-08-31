/*
 * Decodes the ?params= payload produced by circlechiffon/renderers/b50_share.py
 * and renders a sortable best-50 detail table, plus a dxrating.net-compatible
 * JSON export. No build step, no framework - see CLAUDE.md's static-site
 * convention. Keep the payload layout, enum orderings, and rating formula
 * below in sync with:
 *   - circlechiffon/renderers/b50_share.py (payload encoder)
 *   - circlechiffon/songdata/catalog.py (song ordering / index_of)
 *   - circlechiffon/ratingcalc/calculator.py (rating formula)
 */

const FORMAT_VERSION = 1;
const DXDATA_URL = "data/dxdata.json";

const DIFFICULTY_NAMES = ["basic", "advanced", "expert", "master", "remaster"];
const DIFFICULTY_DISPLAY = {
	basic: "BASIC",
	advanced: "ADVANCED",
	expert: "EXPERT",
	master: "MASTER",
	remaster: "Re:MASTER",
};
const CHART_TYPE_NAMES = ["dx", "std"];
const COMBO_NAMES = [null, "fc", "fcp", "ap", "app"];
const SYNC_NAMES = [null, "sync", "fs", "fsp", "fsd", "fsdp"];
const SYNC_DISPLAY = { sync: "SYNC", fs: "FS", fsp: "FS+", fsd: "FDX", fsdp: "FDX+" };

// mirrors renderers/b50.py's _DIFFICULTY_COLOR / _REMASTER_BG / _REMASTER_FG
const DIFFICULTY_COLOR = {
	basic: "#22bb5b",
	advanced: "#fb9c2d",
	expert: "#f64861",
	master: "#9e45e2",
	remaster: "#951BEF",
};
const REMASTER_BG = "#EBCFFF";

// mirrors adapters/maimai_net/badge_icons.py's COMBO_FILES / SYNC_FILES
const BADGE_BASE_URL = "https://maimaidx-eng.com/maimai-mobile/img";
const COMBO_ICON_FILE = { fc: "music_icon_fc", fcp: "music_icon_fcp", ap: "music_icon_ap", app: "music_icon_app" };
const SYNC_ICON_FILE = {
	sync: "music_icon_sync",
	fs: "music_icon_fs",
	fsp: "music_icon_fsp",
	fsd: "music_icon_fdx",
	fsdp: "music_icon_fdxp",
};

const JACKET_BASE_URL = "https://shama.dxrating.net/images/cover/v2";

// mirrors ratingcalc/calculator.py's SCORE_COEFFICIENT_TABLE
const SCORE_COEFFICIENT_TABLE = [
	[0, 0],
	[10, 1.6],
	[20, 3.2],
	[30, 4.8],
	[40, 6.4],
	[50, 8],
	[60, 9.6],
	[70, 11.2],
	[75, 12.0],
	[79.9999, 12.8],
	[80, 13.6],
	[90, 15.2],
	[94, 16.8],
	[96.9999, 17.6],
	[97, 20],
	[98, 20.3],
	[98.9999, 20.6],
	[99, 20.8],
	[99.5, 21.1],
	[99.9999, 21.4],
	[100, 21.6],
	[100.4999, 22.2],
	[100.5, 22.4],
];

function calculateRating(internalLevel, achievement, comboFlag) {
	if (internalLevel == null) return null;
	const table = SCORE_COEFFICIENT_TABLE;
	for (let i = 0; i < table.length; i++) {
		const isLast = i === table.length - 1;
		const nextBreakpoint = isLast ? null : table[i + 1][0];
		if (isLast || achievement < nextBreakpoint) {
			const coefficient = table[i][1];
			const apBonus = comboFlag === "ap" || comboFlag === "app" ? 1 : 0;
			return Math.floor((coefficient * internalLevel * Math.min(100.5, achievement)) / 100) + apBonus;
		}
	}
	return 0;
}

// --- base64url + bit reading ---------------------------------------------

function base64UrlDecode(str) {
	let b64 = str.replace(/-/g, "+").replace(/_/g, "/");
	while (b64.length % 4 !== 0) b64 += "=";
	const binary = atob(b64);
	const bytes = new Uint8Array(binary.length);
	for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
	return bytes;
}

class BitReader {
	constructor(bytes) {
		this.bytes = bytes;
		this.bitPos = 0;
	}
	readBits(n) {
		let value = 0;
		for (let i = 0; i < n; i++) {
			const byteIndex = this.bitPos >> 3;
			const bitIndex = 7 - (this.bitPos & 7);
			const bit = (this.bytes[byteIndex] >> bitIndex) & 1;
			value = (value << 1) | bit;
			this.bitPos++;
		}
		return value >>> 0;
	}
}

// --- CRC32 (mirrors zlib.crc32, used for the catalog freshness stamp) ----

let CRC_TABLE = null;
function crc32(str) {
	if (!CRC_TABLE) {
		CRC_TABLE = new Uint32Array(256);
		for (let n = 0; n < 256; n++) {
			let c = n;
			for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
			CRC_TABLE[n] = c >>> 0;
		}
	}
	const bytes = new TextEncoder().encode(str);
	let crc = 0xffffffff;
	for (let i = 0; i < bytes.length; i++) {
		crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
	}
	return (crc ^ 0xffffffff) >>> 0;
}

// --- payload decode ---------------------------------------------------

function decodePayload(bytes) {
	if (bytes.length < 5) throw new Error("Payload too short.");
	const formatVersion = bytes[0];
	if (formatVersion !== FORMAT_VERSION) {
		throw new Error(`Unsupported payload format version ${formatVersion} (this page understands version ${FORMAT_VERSION}).`);
	}
	const catalogStamp = (bytes[1] << 8) | bytes[2];
	const b15Count = bytes[3];
	const b35Count = bytes[4];

	const reader = new BitReader(bytes.subarray(5));
	const entries = [];
	const total = b15Count + b35Count;
	for (let i = 0; i < total; i++) {
		const songIndex = reader.readBits(12);
		const chartType = CHART_TYPE_NAMES[reader.readBits(1)];
		const difficulty = DIFFICULTY_NAMES[reader.readBits(3)];
		const achievement = reader.readBits(21) / 10000;
		const comboFlag = COMBO_NAMES[reader.readBits(3)];
		const syncFlag = SYNC_NAMES[reader.readBits(3)];
		entries.push({
			bucket: i < b15Count ? "B15" : "B35",
			songIndex,
			chartType,
			difficulty,
			achievement,
			comboFlag,
			syncFlag,
		});
	}
	return { catalogStamp, entries };
}

// --- catalog load -------------------------------------------------------

async function loadCatalog() {
	const resp = await fetch(DXDATA_URL);
	if (!resp.ok) throw new Error(`Couldn't load song catalog (HTTP ${resp.status}).`);
	const raw = await resp.json();
	// same filter as SongCatalog.__init__: drop entries with no real title,
	// preserve raw array order (this order is the encoding's song_index).
	const songs = (raw.songs || []).filter((s) => s.title && s.title.trim().length > 0);
	const stamp = raw.updateTime ? crc32(raw.updateTime) & 0xffff : 0;
	return { songs, stamp, updateTime: raw.updateTime };
}

function findSheet(song, chartType, difficulty) {
	return (song.sheets || []).find((sh) => sh.type === chartType && sh.difficulty === difficulty) || null;
}

// --- rendering ------------------------------------------------------------

function el(tag, attrs, children) {
	const node = document.createElement(tag);
	for (const [k, v] of Object.entries(attrs || {})) {
		if (k === "class") node.className = v;
		else if (k === "text") node.textContent = v;
		else node.setAttribute(k, v);
	}
	for (const child of children || []) node.appendChild(child);
	return node;
}

function difficultyPill(difficulty) {
	const bg = difficulty === "remaster" ? REMASTER_BG : DIFFICULTY_COLOR[difficulty] || "#666666";
	const fg = difficulty === "remaster" ? DIFFICULTY_COLOR.remaster : "#ffffff";
	return el("span", { class: "diff-pill", style: `background:${bg};color:${fg};` }, [
		document.createTextNode(DIFFICULTY_DISPLAY[difficulty] || difficulty),
	]);
}

function badgeImg(fileMap, flag) {
	if (!flag || !fileMap[flag]) return null;
	return el("img", { class: "flag-badge", src: `${BADGE_BASE_URL}/${fileMap[flag]}.png`, alt: flag, loading: "lazy" }, []);
}

function buildRow(row) {
	const tr = document.createElement("tr");
	tr.classList.add(row.bucket === "B15" ? "row-new" : "row-old");

	tr.appendChild(el("td", { class: "bucket-cell" }, [document.createTextNode(row.bucket)]));

	const jacketTd = el("td", {}, []);
	if (row.imageName) {
		jacketTd.appendChild(
			el("img", { class: "jacket-thumb", src: `${JACKET_BASE_URL}/${row.imageName}.jpg`, alt: "", loading: "lazy" }, [])
		);
	}
	tr.appendChild(jacketTd);

	tr.appendChild(el("td", { class: "title-cell" }, [document.createTextNode(row.title)]));
	tr.appendChild(el("td", {}, [difficultyPill(row.difficulty)]));
	tr.appendChild(el("td", { class: "category-cell" }, [document.createTextNode(row.category || "")]));
	tr.appendChild(el("td", { class: "achievement-cell" }, [document.createTextNode(`${row.achievement.toFixed(4)}%`)]));

	const flagsTd = el("td", { class: "flags-cell" }, []);
	const comboBadge = badgeImg(COMBO_ICON_FILE, row.comboFlag);
	const syncBadge = badgeImg(SYNC_ICON_FILE, row.syncFlag);
	if (comboBadge) flagsTd.appendChild(comboBadge);
	if (syncBadge) flagsTd.appendChild(syncBadge);
	tr.appendChild(flagsTd);

	tr.appendChild(el("td", { class: "rating-cell" }, [document.createTextNode(row.rating != null ? String(row.rating) : "-")]));

	return tr;
}

const SORT_ACCESSORS = {
	bucket: (r) => r.bucket,
	title: (r) => r.title.toLowerCase(),
	difficulty: (r) => DIFFICULTY_NAMES.indexOf(r.difficulty),
	category: (r) => (r.category || "").toLowerCase(),
	achievement: (r) => r.achievement,
	flags: (r) => `${r.comboFlag || ""}${r.syncFlag || ""}`,
	rating: (r) => (r.rating != null ? r.rating : -1),
};

function renderTable(rows) {
	const tbody = document.getElementById("b50-tbody");
	tbody.innerHTML = "";
	for (const row of rows) tbody.appendChild(buildRow(row));
}

function setupSorting(rows) {
	let sortKey = null;
	let sortAsc = true;
	document.querySelectorAll("#b50-table th[data-sort]").forEach((th) => {
		th.addEventListener("click", () => {
			const key = th.dataset.sort;
			if (!SORT_ACCESSORS[key]) return;
			if (sortKey === key) {
				sortAsc = !sortAsc;
			} else {
				sortKey = key;
				sortAsc = true;
			}
			document.querySelectorAll("#b50-table th[data-sort]").forEach((h) => h.classList.remove("sort-asc", "sort-desc"));
			th.classList.add(sortAsc ? "sort-asc" : "sort-desc");
			const accessor = SORT_ACCESSORS[sortKey];
			rows.sort((a, b) => {
				const av = accessor(a);
				const bv = accessor(b);
				if (av < bv) return sortAsc ? -1 : 1;
				if (av > bv) return sortAsc ? 1 : -1;
				return 0;
			});
			renderTable(rows);
		});
	});
}

// --- dxrating JSON export --------------------------------------------------

function buildDxratingExport(rows) {
	// dxrating.net's own export order is B35 entries first, then B15 -
	// see ExportToJSONMenuItem.tsx - which differs from our own transport
	// order (B15 first), so re-sort here to match on the way out.
	const ordered = [...rows].filter((r) => r.bucket === "B35").concat(rows.filter((r) => r.bucket === "B15"));
	return ordered.map((r) => ({
		sheetId: `${r.songId}__dxrt__${r.chartType}__dxrt__${r.difficulty}`,
		achievementRate: r.achievement,
		comboFlag: r.comboFlag || null,
		syncFlag: r.syncFlag || null,
	}));
}

function downloadJson(data, filename) {
	const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = url;
	a.download = filename;
	document.body.appendChild(a);
	a.click();
	document.body.removeChild(a);
	URL.revokeObjectURL(url);
}

// --- main -------------------------------------------------------------

function showStatus(message) {
	document.getElementById("status-area").innerHTML = `<p>${message}</p>`;
}

function showWarning(message) {
	const banner = document.getElementById("warning-banner");
	banner.textContent = message;
	banner.classList.remove("d-none");
}

async function main() {
	const params = new URLSearchParams(location.search);
	const paramsValue = params.get("params");
	if (!paramsValue) {
		showStatus("No data provided. Open this page via the “View Detailed List” button under a /cc-best result in Discord.");
		return;
	}

	let payload;
	try {
		payload = decodePayload(base64UrlDecode(paramsValue));
	} catch (err) {
		showStatus(`Couldn't read this link's data: ${err.message}`);
		return;
	}

	let catalog;
	try {
		catalog = await loadCatalog();
	} catch (err) {
		showStatus(`Couldn't load the song catalog: ${err.message}`);
		return;
	}

	if (catalog.stamp !== payload.catalogStamp) {
		showWarning(
			"This link was generated with a different song catalog version than the one currently loaded - some entries may be missing or mismatched."
		);
	}

	let skipped = 0;
	const rows = [];
	for (const entry of payload.entries) {
		const song = catalog.songs[entry.songIndex];
		if (!song) {
			skipped++;
			continue;
		}
		const sheet = findSheet(song, entry.chartType, entry.difficulty);
		const internalLevel = sheet ? sheet.internalLevelValue : null;
		rows.push({
			bucket: entry.bucket,
			songId: song.songId,
			title: song.title,
			category: song.category,
			imageName: song.imageName,
			chartType: entry.chartType,
			difficulty: entry.difficulty,
			achievement: entry.achievement,
			comboFlag: entry.comboFlag,
			syncFlag: entry.syncFlag,
			rating: calculateRating(internalLevel, entry.achievement, entry.comboFlag),
		});
	}

	if (skipped > 0) {
		showWarning(
			`${skipped} chart${skipped === 1 ? "" : "s"} couldn't be resolved against the currently-loaded catalog and ${
				skipped === 1 ? "was" : "were"
			} skipped.`
		);
	}

	if (rows.length === 0) {
		showStatus("No charts could be resolved from this link.");
		return;
	}

	document.getElementById("status-area").classList.add("d-none");
	document.getElementById("result-area").classList.remove("d-none");

	const b15Count = rows.filter((r) => r.bucket === "B15").length;
	const b35Count = rows.filter((r) => r.bucket === "B35").length;
	document.getElementById("summary-text").textContent = `${rows.length} charts (B15: ${b15Count}, B35: ${b35Count})`;

	renderTable(rows);
	setupSorting(rows);

	document.getElementById("export-json-btn").addEventListener("click", () => {
		downloadJson(buildDxratingExport(rows), "best50-dxrating-export.json");
	});
}

main();
