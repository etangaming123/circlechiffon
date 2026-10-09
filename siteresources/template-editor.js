// Drag-and-drop editor for CiRCLE Chiffon's custom render templates
// (docs/customisation.md). Runs entirely client-side: images are shown via
// object URLs and never leave the browser. The per-kind defaults come from
// template-layouts.js, generated from the Python renderers by
// generate_templates.py, so this page and the bot agree on every element.
//
// Coordinates are the render's own output pixels, the same numbers the bot
// uses. The stage is drawn at that size and CSS-scaled to the zoom level.

(function () {
	"use strict";

	const KINDS = window.CC_TEMPLATE_KINDS;
	const STORAGE_PREFIX = "cc-template-editor:";
	const COLORS = ["#ff4fd8", "#35c8ff", "#ffc83d", "#3ee07e", "#ff8a3d", "#b88cff"];
	const MIN_SIZE = 4;

	const $ = (id) => document.getElementById(id);
	const els = {
		kind: $("kind-select"),
		viewToggle: $("view-toggle"),
		canvasSize: $("canvas-size"),
		baseInput: $("base-input"),
		topInput: $("top-input"),
		baseName: $("base-name"),
		topName: $("top-name"),
		baseClear: $("base-clear"),
		topClear: $("top-clear"),
		modeEdit: $("mode-edit"),
		modeLive: $("mode-live"),
		liveNote: $("live-note"),
		labelsToggle: $("labels-toggle"),
		undoBtn: $("undo-btn"),
		redoBtn: $("redo-btn"),
		guideBtn: $("guide-btn"),
		welcome: $("welcome"),
		welcomeGuide: $("welcome-guide"),
		welcomeDismiss: $("welcome-dismiss"),
		filter: $("element-filter"),
		textStyleNote: $("text-style-note"),
		showBase: $("show-base"),
		showTop: $("show-top"),
		showLabels: $("show-labels"),
		exportBtn: $("export-btn"),
		importInput: $("import-input"),
		resetBtn: $("reset-btn"),
		snap: $("snap-input"),
		options: $("options"),
		status: $("status"),
		selTitle: $("selection-title"),
		selHint: $("selection-hint"),
		selFields: $("selection-fields"),
		fx: $("f-x"),
		fy: $("f-y"),
		fw: $("f-w"),
		fh: $("f-h"),
		fVisible: $("f-visible"),
		followRow: $("follow-row"),
		fFollow: $("f-follow"),
		fGap: $("f-gap"),
		geometry: $("geometry-fields"),
		multiNote: $("multi-note"),
		colors: $("colors"),
		colorsPanel: $("colors-panel"),
		selectAll: $("select-all"),
		selectNone: $("select-none"),
		fOpacity: $("f-opacity"),
		fOpacityVal: $("f-opacity-val"),
		textStyle: $("text-style"),
		textPreview: $("text-preview"),
		fColor: $("f-color"),
		fColorReset: $("f-color-reset"),
		fOutlineColor: $("f-outline-color"),
		fOutlineColorReset: $("f-outline-color-reset"),
		fOutlineWidth: $("f-outline-width"),
		fOutlineWidthVal: $("f-outline-width-val"),
		fOutlineWidthReset: $("f-outline-width-reset"),
		resizeNote: $("resize-note"),
		list: $("element-list"),
		scroll: $("stage-scroll"),
		wrap: $("stage-wrap"),
		stage: $("stage"),
		baseLayer: $("base-layer"),
		topLayer: $("top-layer"),
		contextLayer: $("context-layer"),
		elementLayer: $("element-layer"),
	};

	const state = {
		kind: Object.keys(KINDS)[0],
		view: "page", // "page" | "card" (b50 only)
		layouts: {}, // kind -> working layout (full, defaults-shaped)
		selected: null, // the primary (last-clicked) element
		sel: new Set(), // every selected element, primary included
		zoom: "fit",
		live: false, // preview mode: placeholder text instead of bare boxes
		tab: "template",
		images: { base: null, top: null }, // object URLs, per session only
	};

	// ---- storage (a per-viewer convenience only; the page works without it)

	function loadSaved(kind) {
		try {
			const raw = window.localStorage.getItem(STORAGE_PREFIX + kind);
			return raw ? JSON.parse(raw) : null;
		} catch (e) {
			return null;
		}
	}

	function save() {
		commit();
		try {
			window.localStorage.setItem(STORAGE_PREFIX + state.kind, JSON.stringify(layout()));
		} catch (e) {
			// private mode / storage blocked - nothing to do
		}
	}

	// per-viewer UI prefs (live mode, welcome banner); never required
	function loadUi() {
		try {
			return JSON.parse(window.localStorage.getItem(STORAGE_PREFIX + "ui")) || {};
		} catch (e) {
			return {};
		}
	}

	function saveUi(patch) {
		try {
			window.localStorage.setItem(STORAGE_PREFIX + "ui", JSON.stringify(Object.assign(loadUi(), patch)));
		} catch (e) {
			// ignore
		}
	}

	// ---- undo / redo: JSON snapshots per kind. Edits landing within
	// COALESCE_MS of each other (a slider drag, held arrow key) share one step.

	const COALESCE_MS = 500;
	const MAX_HISTORY = 100;
	const history = {}; // kind -> { past: [], future: [], present: string, at: ms }

	function resetHistory() {
		history[state.kind] = { past: [], future: [], present: JSON.stringify(layout()), at: 0 };
		updateHistoryButtons();
	}

	function commit() {
		const h = history[state.kind];
		if (!h || !state.layouts[state.kind]) return;
		const now = JSON.stringify(layout());
		if (now === h.present) return;
		const t = Date.now();
		if (t - h.at > COALESCE_MS) {
			h.past.push(h.present);
			if (h.past.length > MAX_HISTORY) h.past.shift();
		}
		h.present = now;
		h.at = t;
		h.future.length = 0;
		updateHistoryButtons();
	}

	function updateHistoryButtons() {
		const h = history[state.kind];
		els.undoBtn.disabled = !h || !h.past.length;
		els.redoBtn.disabled = !h || !h.future.length;
	}

	function stepHistory(dir) {
		const h = history[state.kind];
		if (!h) return;
		const from = dir < 0 ? h.past : h.future;
		if (!from.length) return;
		(dir < 0 ? h.future : h.past).push(h.present);
		h.present = from.pop();
		h.at = 0;
		state.layouts[state.kind] = JSON.parse(h.present);
		try {
			window.localStorage.setItem(STORAGE_PREFIX + state.kind, h.present);
		} catch (e) {
			// ignore
		}
		updateHistoryButtons();
		renderOptions();
		renderColors();
		renderStage();
	}

	// What the Live preview shows in each text element: per kind, per view.
	const PLACEHOLDERS = {
		b50: {
			page: {
				name: "\uFF23\uFF49\uFF32\uFF23\uFF2C\uFF25",
				stat_total: "999,999",
				stat_b15: "99,999",
				stat_b35: "999,999",
				section_b35: "BEST 35 \u00B7 OLDER VERSIONS",
				section_b15: "BEST 15 \u00B7 CURRENT VERSION",
			},
			card: {
				type_tag: "DX",
				level_badge: "15+",
				title: "A really long chart name powered by CiRCLE Chiffon",
				achievement: "100.5000%",
				difficulty_name: "Re:MASTER",
				rating_value: "999",
				rank_number: "#50",
			},
		},
	};
	// font size as a fraction of the box height (the bot sizes text from the box)
	const TEXT_RATIO = { stat_total: 0.55, stat_b15: 0.55, stat_b35: 0.55, achievement: 0.8, rating_value: 0.8, title: 0.78 };
	const DEFAULT_TEXT_RATIO = 0.72;
	const placeholder = (name) => {
		const set = ((PLACEHOLDERS[state.kind] || {})[isCardView() ? "card" : "page"]) || {};
		return set[name] || labels()[name] || name;
	};

	// ---- layout helpers

	const clone = (o) => JSON.parse(JSON.stringify(o));
	const spec = () => KINDS[state.kind];
	const layout = () => state.layouts[state.kind];
	const isCardView = () => state.view === "card" && !!spec().defaults.card;
	const group = () => (isCardView() ? layout().card.elements : layout().elements);
	const labels = () => (isCardView() ? spec().cardLabels : spec().labels);
	const canvasSize = () => (isCardView() ? spec().defaults.card.canvas : spec().defaults.canvas);
	const resizeMode = (name) => spec().resize[name] || "both";
	const isText = (name) => ((isCardView() ? spec().cardTextElements : spec().textElements) || []).includes(name);
	const HEX = /^#[0-9a-fA-F]{6}$/;
	const MAX_OUTLINE = 32;

	// Merge an imported (or saved) layout over the defaults: only known
	// elements/keys, numbers scaled from the canvas it was authored at.
	function mergeLayout(kind, incoming) {
		const defaults = clone(KINDS[kind].defaults);
		if (!incoming || typeof incoming !== "object") return defaults;
		let scale = 1;
		if (Array.isArray(incoming.canvas) && incoming.canvas[0] > 0) {
			scale = defaults.canvas[0] / incoming.canvas[0];
		}
		const mergeGroup = (target, source) => {
			if (!source || typeof source !== "object") return;
			for (const [name, el] of Object.entries(source)) {
				if (!target[name] || !el || typeof el !== "object") continue;
				for (const key of ["x", "y", "w", "h", "gap"]) {
					if (typeof el[key] === "number" && isFinite(el[key])) target[name][key] = Math.round(el[key] * scale);
				}
				if (typeof el.visible === "boolean") target[name].visible = el.visible;
				if (typeof el.opacity === "number" && isFinite(el.opacity)) target[name].opacity = Math.min(1, Math.max(0, el.opacity));
				for (const key of ["color", "outline_color"]) {
					if (typeof el[key] === "string" && HEX.test(el[key])) target[name][key] = el[key].toLowerCase();
				}
				if (typeof el.outline_width === "number" && isFinite(el.outline_width)) {
					target[name].outline_width = Math.min(MAX_OUTLINE, Math.max(0, el.outline_width));
				}
				if ("follow" in el) target[name].follow = el.follow && target[el.follow] && el.follow !== name ? el.follow : null;
			}
		};
		mergeGroup(defaults.elements, incoming.elements);
		if (defaults.card && incoming.card) mergeGroup(defaults.card.elements, incoming.card.elements);
		if (defaults.colors && incoming.colors && typeof incoming.colors === "object") {
			for (const slot of Object.keys(defaults.colors)) {
				if (typeof incoming.colors[slot] === "string" && HEX.test(incoming.colors[slot])) {
					defaults.colors[slot] = incoming.colors[slot].toLowerCase();
				}
			}
		}
		if (defaults.opacities && incoming.opacities && typeof incoming.opacities === "object") {
			for (const slot of Object.keys(defaults.opacities)) {
				const v = incoming.opacities[slot];
				if (typeof v === "number" && isFinite(v)) defaults.opacities[slot] = Math.min(1, Math.max(0, v));
			}
		}
		if (defaults.widths && incoming.widths && typeof incoming.widths === "object") {
			for (const slot of Object.keys(defaults.widths)) {
				const v = incoming.widths[slot];
				if (typeof v === "number" && isFinite(v)) defaults.widths[slot] = Math.min(MAX_OUTLINE, Math.max(0, v));
			}
		}
		if (incoming.options && typeof incoming.options === "object") {
			for (const key of Object.keys(defaults.options || {})) {
				if (typeof incoming.options[key] === "boolean") defaults.options[key] = incoming.options[key];
			}
		}
		return defaults;
	}

	// Where an element shows up: a `follow`ing element sits right after its
	// leader's box. (The bot uses where the leader's content actually ends,
	// e.g. the end of the name text, so it can land a little further left.)
	function effectiveX(name, seen) {
		const g = group();
		const el = g[name];
		seen = seen || new Set();
		if (!el.follow || !g[el.follow] || seen.has(el.follow)) return el.x;
		seen.add(name);
		const leader = g[el.follow];
		if (leader.visible === false) return effectiveX(el.follow, seen);
		return effectiveX(el.follow, seen) + leader.w + (el.gap || 0);
	}

	// ---- rendering

	let zoomFactor = 1;

	function computeZoom() {
		const [w] = canvasSize();
		if (state.zoom === "fit") {
			const available = els.scroll.clientWidth - 2;
			return Math.min(1, available / w);
		}
		return Number(state.zoom);
	}

	function renderStage() {
		const [w, h] = canvasSize();
		zoomFactor = computeZoom();
		els.stage.style.width = w + "px";
		els.stage.style.height = h + "px";
		els.stage.style.transform = `scale(${zoomFactor})`;
		els.stage.style.setProperty("--inv", String(1 / zoomFactor));
		els.wrap.style.width = w * zoomFactor + "px";
		els.wrap.style.height = h * zoomFactor + "px";
		els.stage.classList.toggle("hide-labels", !els.showLabels.checked);
		els.stage.classList.toggle("live", state.live);
		els.canvasSize.textContent = isCardView()
			? `Card: ${w}×${h}px, shared by all 50 cards. Positions are relative to the card's top-left.`
			: `Canvas: ${w}×${h}px` + (spec().fixedSize ? " (images are stretched to this)" : " (images fit the width; height grows with the lists)");
		renderImages();
		renderContext();
		renderElements();
	}

	function applyImage(layer, url) {
		if (!url) {
			layer.style.backgroundImage = "none";
			return;
		}
		layer.style.backgroundImage = `url("${url}")`;
		const [pw, ph] = spec().defaults.canvas;
		if (isCardView()) {
			// the part of the page image behind card #1 of the B35 grid
			const grid = layout().elements.grid_b35;
			const pad = spec().grid.cellPadding;
			layer.style.backgroundSize = `${pw}px ${ph}px`;
			layer.style.backgroundPosition = `${-(grid.x + pad)}px ${-(grid.y + pad)}px`;
		} else if (spec().fixedSize) {
			layer.style.backgroundSize = "100% 100%";
			layer.style.backgroundPosition = "0 0";
		} else {
			layer.style.backgroundSize = "100% auto";
			layer.style.backgroundPosition = "0 0";
		}
	}

	function renderImages() {
		applyImage(els.baseLayer, els.showBase.checked ? state.images.base : null);
		applyImage(els.topLayer, els.showTop.checked ? state.images.top : null);
	}

	// Non-editable context: every card slot inside the b50 grids (page
	// view), or the stock card background (card view).
	function renderContext() {
		els.contextLayer.replaceChildren();
		const grid = spec().grid;
		if (!grid) return;
		const [cw, ch] = spec().defaults.card.canvas;
		if (isCardView()) {
			if (layout().options.card_background) {
				const card = document.createElement("div");
				card.className = "ctx-card";
				Object.assign(card.style, { left: "0px", top: "0px", width: cw + "px", height: ch + "px" });
				els.contextLayer.appendChild(card);
			}
			return;
		}
		for (const [name, cols] of Object.entries(grid.cols)) {
			const g = layout().elements[name];
			if (!g || g.visible === false) continue;
			for (let i = 0; i < cols * grid.rows; i++) {
				const box = document.createElement("div");
				box.className = "ctx-box";
				Object.assign(box.style, {
					left: g.x + (i % cols) * grid.cell[0] + grid.cellPadding + "px",
					top: g.y + Math.floor(i / cols) * grid.cell[1] + grid.cellPadding + "px",
					width: cw + "px",
					height: ch + "px",
				});
				els.contextLayer.appendChild(box);
			}
		}
	}

	function renderElements() {
		els.elementLayer.replaceChildren();
		els.list.replaceChildren();
		const g = group();
		Object.keys(labels()).forEach((name, i) => {
			const el = g[name];
			const color = COLORS[i % COLORS.length];

			const node = document.createElement("div");
			node.className = "el";
			node.dataset.name = name;
			node.style.setProperty("--c", color);
			node.classList.toggle("follows", !!el.follow);
			node.classList.toggle("hidden-element", el.visible === false);
			node.classList.toggle("selected", state.sel.has(name));
			const isTxt = isText(name);
			node.classList.toggle("text-el", isTxt);
			node.classList.toggle("image-el", !isTxt);
			if (name === "rating_badge" || name === "rating_value" || name === "achievement" || name === "level_badge" || name === "stat_total" || name === "stat_b15" || name === "stat_b35") {
				node.classList.add(name.startsWith("stat_") ? "align-end" : "align-center");
			}
			// edit mode keeps faint boxes findable; live shows the true opacity
			node.style.opacity = el.opacity == null ? "" : String(state.live ? el.opacity : Math.max(0.15, el.opacity));
			Object.assign(node.style, {
				left: effectiveX(name) + "px",
				top: el.y + "px",
				width: el.w + "px",
				height: el.h + "px",
			});
			const label = document.createElement("span");
			label.className = "el-label";
			label.textContent = labels()[name];
			node.appendChild(label);
			if (isTxt) {
				const wrapText = document.createElement("div");
				wrapText.className = "el-text";
				const run = document.createElement("span");
				run.textContent = placeholder(name);
				run.style.fontSize = Math.max(4, el.h * (TEXT_RATIO[name] || DEFAULT_TEXT_RATIO)) + "px";
				run.style.color = el.color || "#ffffff";
				const ow = el.outline_width == null ? 0 : el.outline_width;
				run.style.webkitTextStroke = ow ? `${ow * (el.h / 40)}px ${el.outline_color || "#141414"}` : "0";
				wrapText.appendChild(run);
				node.appendChild(wrapText);
			}
			const mode = resizeMode(name);
			const dirs = mode === "both" ? ["e", "s", "se"] : mode === "x" ? ["e"] : [];
			for (const dir of dirs) {
				const handle = document.createElement("div");
				handle.className = "handle";
				handle.dataset.dir = dir;
				node.appendChild(handle);
			}
			node.addEventListener("pointerdown", onPointerDown);
			els.elementLayer.appendChild(node);

			const item = document.createElement("li");
			item.classList.toggle("selected", state.sel.has(name));
			item.classList.toggle("hidden-element", el.visible === false);
			const box = document.createElement("input");
			box.type = "checkbox";
			box.checked = el.visible !== false;
			box.title = "Visible";
			box.setAttribute("aria-label", `${labels()[name]} visible`);
			box.addEventListener("click", (ev) => ev.stopPropagation());
			box.addEventListener("change", () => {
				el.visible = box.checked;
				changed();
			});
			const swatch = document.createElement("span");
			swatch.style.cssText = `display:inline-block;width:10px;height:10px;border-radius:2px;background:${color}`;
			const text = document.createElement("span");
			text.className = "name";
			text.textContent = labels()[name];
			item.append(box, swatch, text);
			const q = els.filter.value.trim().toLowerCase();
			item.classList.toggle("d-none", !!q && !labels()[name].toLowerCase().includes(q));
			item.addEventListener("click", (ev) => select(name, ev.shiftKey || ev.ctrlKey || ev.metaKey));
			item.addEventListener("mouseenter", () => node.classList.add("hover"));
			item.addEventListener("mouseleave", () => node.classList.remove("hover"));
			els.list.appendChild(item);
		});
		renderSelection();
	}

	function renderSelection() {
		const name = state.selected;
		const el = name && group()[name];
		if (!el) {
			els.selTitle.textContent = "Inspector";
			els.selHint.classList.remove("d-none");
			els.selFields.classList.add("d-none");
			return;
		}
		const multi = state.sel.size > 1;
		els.selTitle.textContent = multi ? `${state.sel.size} elements selected` : labels()[name];
		els.selHint.classList.add("d-none");
		els.selFields.classList.remove("d-none");
		els.geometry.classList.toggle("d-none", multi);
		if (multi) els.resizeNote.textContent = "";
		els.multiNote.classList.toggle("d-none", !multi);
		els.fx.value = effectiveX(name);
		els.fy.value = el.y;
		els.fw.value = el.w;
		els.fh.value = el.h;
		const mode = resizeMode(name);
		els.fw.disabled = mode === "none";
		els.fh.disabled = mode !== "both";
		els.resizeNote.textContent = multi ? "" :
			mode === "none" ? "This element can only be moved - its size is fixed." :
			mode === "x" ? "Only the width of this element can change." :
			el.follow ? "Follows another element - dragging it sideways detaches it." : "";
		els.fVisible.checked = el.visible !== false;

		els.fFollow.replaceChildren();
		const none = new Option("(none - fixed x)", "");
		els.fFollow.appendChild(none);
		for (const other of Object.keys(labels())) {
			if (other !== name) els.fFollow.appendChild(new Option(labels()[other], other));
		}
		els.fFollow.value = el.follow || "";
		els.fGap.value = el.gap || 0;
		els.fGap.disabled = !el.follow;

		const opacities = new Set([...state.sel].map((n) => Math.round((group()[n].opacity == null ? 1 : group()[n].opacity) * 100)));
		const opacity = Math.round((el.opacity == null ? 1 : el.opacity) * 100);
		els.fOpacity.value = opacity;
		els.fOpacityVal.textContent = opacities.size > 1 ? "(mixed - slide to set all)" : opacity + "%";
		// the bot ignores opacity on grids
		els.fOpacity.disabled = [...state.sel].every((n) => n.startsWith("grid_"));
		const text = [...state.sel].every(isText);
		// always present, just disabled for non-text, so the panel never reflows
		els.textStyle.disabled = !text;
		els.textPreview.textContent = text ? placeholder(name) : "Aa 123";
		if (!text) {
			els.textPreview.style.color = "#8a90a8";
			els.textPreview.style.webkitTextStroke = "0";
			els.textPreview.style.opacity = "1";
		} else {
			els.fColor.value = el.color || "#ffffff";
			els.fOutlineColor.value = el.outline_color || "#141414";
			els.fOutlineWidth.value = el.outline_width == null ? 0 : el.outline_width;
			els.fOutlineWidthVal.textContent = el.outline_width == null ? "default" : el.outline_width + "px";
			// preview: shown on a checker so it's legible whatever the colour; scaled to a fixed size
			els.textPreview.style.color = el.color || "#ffffff";
			els.textPreview.style.webkitTextStroke = `${(el.outline_width || 0) * 1.5}px ${el.outline_color || "#141414"}`;
			els.textPreview.style.paintOrder = "stroke fill";
			els.textPreview.style.opacity = String(el.opacity == null ? 1 : el.opacity);
		}
	}

	// ---- interaction

	// Plain click selects one; Shift/Ctrl/Cmd-click adds to / removes from the selection.
	function select(name, additive) {
		if (!name) {
			state.selected = null;
			state.sel.clear();
		} else if (additive) {
			if (state.sel.has(name)) {
				state.sel.delete(name);
				state.selected = state.sel.size ? [...state.sel].pop() : null;
			} else {
				state.sel.add(name);
				state.selected = name;
			}
		} else {
			state.sel = new Set([name]);
			state.selected = name;
		}
		renderElements();
	}

	const selectedEls = () => [...state.sel].map((n) => group()[n]).filter(Boolean);

	function snap(v) {
		const s = Number(els.snap.value) || 0;
		return s > 0 ? Math.round(v / s) * s : Math.round(v);
	}

	function changed() {
		save();
		renderStage();
	}

	let drag = null;

	function onPointerDown(ev) {
		const node = ev.currentTarget;
		const name = node.dataset.name;
		const el = group()[name];
		ev.preventDefault();
		els.stage.focus({ preventScroll: true });
		if (ev.shiftKey || ev.ctrlKey || ev.metaKey) {
			select(name, true); // toggles; no drag
			return;
		}
		if (!state.sel.has(name)) {
			select(name);
			setTab("inspector");
		} else if (state.selected !== name) {
			state.selected = name;
			renderSelection();
		}
		const target = els.elementLayer.querySelector(`.el[data-name="${CSS.escape(name)}"]`);
		drag = {
			name,
			dir: ev.target.classList.contains("handle") ? ev.target.dataset.dir : "move",
			startX: ev.clientX,
			startY: ev.clientY,
			box: { x: effectiveX(name), y: el.y, w: el.w, h: el.h },
			follow: el.follow,
			moved: false,
			// every selected element moves together; remember where each started
			starts: Object.fromEntries([...state.sel].map((n) => [n, { x: effectiveX(n), y: group()[n].y }])),
		};
		target.setPointerCapture(ev.pointerId);
		target.addEventListener("pointermove", onPointerMove);
		target.addEventListener("pointerup", onPointerUp);
		target.addEventListener("pointercancel", onPointerUp);
	}

	function onPointerMove(ev) {
		if (!drag) return;
		const el = group()[drag.name];
		const dx = (ev.clientX - drag.startX) / zoomFactor;
		const dy = (ev.clientY - drag.startY) / zoomFactor;
		if (!drag.moved && Math.abs(dx) + Math.abs(dy) < 2 / zoomFactor) return;
		drag.moved = true;
		if (drag.dir === "move") {
			for (const [n, start] of Object.entries(drag.starts)) {
				const m = group()[n];
				// a follower whose leader moves too just rides along
				const rides = m.follow && state.sel.has(m.follow);
				if (m.follow && !rides && Math.abs(dx) >= 1) m.follow = null; // a sideways drag pins it to a fixed x
				if (!m.follow) m.x = snap(start.x + dx);
				m.y = snap(start.y + dy);
				if (n !== drag.name) updateNode(n);
			}
		} else {
			if (drag.dir.includes("e")) el.w = Math.max(MIN_SIZE, snap(drag.box.w + dx));
			if (drag.dir.includes("s")) el.h = Math.max(MIN_SIZE, snap(drag.box.h + dy));
		}
		// cheap live update of just the dragged nodes
		updateNode(drag.name);
		renderSelection();
	}

	function updateNode(name) {
		const node = els.elementLayer.querySelector(`.el[data-name="${CSS.escape(name)}"]`);
		const el = group()[name];
		if (!node || !el) return;
		Object.assign(node.style, {
			left: effectiveX(name) + "px",
			top: el.y + "px",
			width: el.w + "px",
			height: el.h + "px",
		});
		node.classList.toggle("follows", !!el.follow);
	}

	function onPointerUp(ev) {
		const node = ev.currentTarget;
		node.removeEventListener("pointermove", onPointerMove);
		node.removeEventListener("pointerup", onPointerUp);
		node.removeEventListener("pointercancel", onPointerUp);
		const moved = drag && drag.moved;
		drag = null;
		if (moved) changed();
	}

	// one field at a time, so e.g. typing an x can detach a follow without
	// the (still showing the old value) follow dropdown re-attaching it
	function onField(ev) {
		const name = state.selected;
		const el = name && group()[name];
		if (!el) return;
		const input = ev.target;
		// styling edits apply to the whole selection
		const targets = selectedEls();
		if (input === els.fOpacity) {
			const v = Math.min(100, Math.max(0, Number(input.value)));
			for (const t of targets) {
				if (v >= 100) delete t.opacity; // absent = stock look
				else t.opacity = v / 100;
			}
			changed();
			return;
		}
		if (input === els.fColor || input === els.fOutlineColor || input === els.fOutlineWidth) {
			for (const t of targets) {
				if (input === els.fColor) t.color = input.value;
				else if (input === els.fOutlineColor) t.outline_color = input.value;
				else t.outline_width = Math.min(MAX_OUTLINE, Math.max(0, Number(input.value)));
			}
			changed();
			return;
		}
		if (input === els.fVisible) {
			for (const t of targets) t.visible = input.checked;
			changed();
			return;
		}
		const num = (fallback) => {
			const v = Number(input.value);
			return input.value !== "" && Number.isFinite(v) ? Math.round(v) : fallback;
		};
		if (input === els.fx) {
			const newX = num(effectiveX(name));
			if (newX !== effectiveX(name)) {
				el.follow = null;
				el.x = newX;
			}
		} else if (input === els.fy) {
			el.y = num(el.y);
		} else if (input === els.fw) {
			el.w = Math.max(MIN_SIZE, num(el.w));
		} else if (input === els.fh) {
			el.h = Math.max(MIN_SIZE, num(el.h));
		} else if (input === els.fFollow) {
			if (input.value) {
				// the bot rejects a follow chain that loops back on itself
				for (let cur = input.value, n = 0; cur && n < 100; cur = group()[cur].follow, n++) {
					if (cur === name) {
						setStatus(`Can't follow ${labels()[input.value]}: it already follows ${labels()[name]}.`);
						renderSelection();
						return;
					}
				}
				el.follow = input.value;
			} else {
				// detaching keeps it where it currently shows
				el.x = effectiveX(name);
				el.follow = null;
			}
		} else if (input === els.fGap) {
			el.gap = num(el.gap || 0);
		}
		changed();
	}

	function cycleSelection(dir) {
		const names = Object.keys(labels());
		const i = names.indexOf(state.selected);
		const next = names[(i + dir + names.length) % names.length];
		select(next);
		setTab("inspector");
	}

	function onKey(ev) {
		const tag = (ev.target.tagName || "").toLowerCase();
		if (tag === "input" || tag === "select" || tag === "textarea") return;
		if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "z") {
			ev.preventDefault();
			stepHistory(ev.shiftKey ? 1 : -1);
			return;
		}
		if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "y") {
			ev.preventDefault();
			stepHistory(1);
			return;
		}
		if (!ev.ctrlKey && !ev.metaKey && !ev.altKey) {
			if (ev.key === "l" || ev.key === "L") return setLive(!state.live);
			if (ev.key === "?") return setTab("guide");
			if (ev.key === "[") return cycleSelection(-1);
			if (ev.key === "]") return cycleSelection(1);
		}
		if (ev.key === "Escape") {
			select(null);
			return;
		}
		const step = ev.shiftKey ? 10 : 1;
		const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
		if (!state.sel.size || !moves[ev.key]) return;
		ev.preventDefault();
		const [dx, dy] = moves[ev.key];
		for (const name of state.sel) {
			const el = group()[name];
			if (dx && !(el.follow && state.sel.has(el.follow))) {
				el.x = effectiveX(name) + dx;
				el.follow = null;
			}
			el.y += dy;
		}
		changed();
	}

	// ---- import / export

	let statusTimer = 0;

	function setStatus(text) {
		els.status.textContent = text;
		els.status.classList.toggle("show", !!text);
		clearTimeout(statusTimer);
		if (text) statusTimer = setTimeout(() => els.status.classList.remove("show"), 5000);
	}

	function exportLayout() {
		const l = layout();
		const out = {
			version: l.version,
			kind: l.kind,
			canvas: l.canvas,
			elements: l.elements,
			options: l.options,
		};
		if (l.colors) out.colors = l.colors;
		if (l.opacities) out.opacities = l.opacities;
		if (l.widths) out.widths = l.widths;
		if (l.card) out.card = { elements: l.card.elements };
		const blob = new Blob([JSON.stringify(out, null, 1)], { type: "application/json" });
		const a = document.createElement("a");
		a.href = URL.createObjectURL(blob);
		a.download = `${state.kind}-layout.json`;
		document.body.appendChild(a);
		a.click();
		a.remove();
		setTimeout(() => URL.revokeObjectURL(a.href), 1000);
		setStatus(`Exported ${a.download}. Upload it with /cc-template-upload.`);
	}

	function importLayout(file) {
		const reader = new FileReader();
		reader.onload = () => {
			let data;
			try {
				data = JSON.parse(reader.result);
			} catch (e) {
				setStatus("That file isn't valid JSON.");
				return;
			}
			if (!data || !KINDS[data.kind]) {
				setStatus("That doesn't look like a CiRCLE Chiffon layout (no known \"kind\").");
				return;
			}
			if (data.kind !== state.kind) switchKind(data.kind);
			state.layouts[state.kind] = mergeLayout(state.kind, data);
			state.selected = null;
			state.sel.clear();
			const h = history[state.kind];
			if (h) h.at = 0; // never merge an import into the previous edit's undo step
			changed();
			renderOptions();
			renderColors();
			setStatus(`Imported ${file.name}.`);
		};
		reader.readAsText(file);
	}

	function loadImage(which, file) {
		if (state.images[which]) URL.revokeObjectURL(state.images[which]);
		state.images[which] = file ? URL.createObjectURL(file) : null;
		const name = which === "base" ? els.baseName : els.topName;
		const clear = which === "base" ? els.baseClear : els.topClear;
		name.textContent = file ? file.name : "none";
		name.title = file ? file.name : "";
		clear.classList.toggle("d-none", !file);
		if (file) {
			const img = new Image();
			img.onload = () => {
				const [w, h] = spec().defaults.canvas;
				const note = spec().fixedSize && Math.abs(img.width / img.height - w / h) > 0.02 * (w / h)
					? ` Its aspect ratio doesn't match the ${w}\u00D7${h} canvas, so it will be stretched.`
					: "";
				setStatus(`${which} image: ${img.width}\u00D7${img.height}.${note}`);
			};
			img.src = state.images[which];
		}
		renderImages();
	}

	// ---- tabs, mode, guide

	function setTab(tab) {
		state.tab = tab;
		for (const btn of document.querySelectorAll(".sb-tabs [data-tab]")) {
			const on = btn.dataset.tab === tab;
			btn.classList.toggle("active", on);
			btn.setAttribute("aria-selected", String(on));
		}
		for (const pane of document.querySelectorAll(".sb-pane")) pane.classList.toggle("d-none", pane.id !== "pane-" + tab);
	}

	function setLive(on) {
		state.live = on;
		els.modeEdit.classList.toggle("active", !on);
		els.modeLive.classList.toggle("active", on);
		els.modeEdit.setAttribute("aria-pressed", String(!on));
		els.modeLive.setAttribute("aria-pressed", String(on));
		els.liveNote.classList.toggle("d-none", !on);
		els.labelsToggle.classList.toggle("d-none", on);
		saveUi({ live: on });
		renderStage();
	}

	function dismissWelcome() {
		els.welcome.classList.add("d-none");
		saveUi({ welcomed: true });
	}

	// ---- setup

	// Layout-wide colour slots (e.g. the count pill, class point bar, missions).
	function renderColors() {
		els.colors.replaceChildren();
		const slots = spec().colorLabels || {};
		const names = Object.keys(slots);
		const opacityLabels = spec().opacityLabels || {};
		const widthLabels = spec().widthLabels || {};
		els.colorsPanel.classList.toggle(
			"d-none",
			!names.length && !Object.keys(opacityLabels).length && !Object.keys(widthLabels).length
		);
		const current = layout().colors || {};
		const stock = spec().defaults.colors || {};
		for (const slot of names) {
			const row = document.createElement("div");
			row.className = "color-row";
			const label = document.createElement("label");
			label.textContent = slots[slot];
			const input = document.createElement("input");
			input.type = "color";
			input.value = current[slot] || stock[slot];
			input.setAttribute("aria-label", slots[slot]);
			input.addEventListener("input", () => {
				layout().colors[slot] = input.value;
				save();
			});
			const reset = document.createElement("button");
			reset.type = "button";
			reset.className = "btn btn-outline-light btn-sm";
			reset.textContent = "\u21BA";
			reset.title = "Back to the stock colour";
			reset.addEventListener("click", () => {
				layout().colors[slot] = stock[slot];
				input.value = stock[slot];
				save();
			});
			label.htmlFor = "";
			row.append(input, label, reset);
			els.colors.appendChild(row);
		}

		// layout-wide opacity sliders (e.g. count pill background / icon / text)
		const levels = layout().opacities || {};
		for (const slot of Object.keys(opacityLabels)) {
			const row = document.createElement("div");
			row.className = "opacity-row";
			const label = document.createElement("label");
			const value = document.createElement("span");
			const show = (v) => (value.textContent = Math.round(v * 100) + "%");
			label.append(opacityLabels[slot] + " ", value);
			const input = document.createElement("input");
			input.type = "range";
			input.className = "custom-range";
			input.min = "0";
			input.max = "100";
			input.value = String(Math.round((levels[slot] == null ? 1 : levels[slot]) * 100));
			show(input.value / 100);
			input.addEventListener("input", () => {
				layout().opacities[slot] = input.value / 100;
				show(input.value / 100);
				save();
			});
			row.append(label, input);
			els.colors.appendChild(row);
		}

		// layout-wide pixel widths (e.g. count text outline)
		const widths = layout().widths || {};
		for (const slot of Object.keys(widthLabels)) {
			const row = document.createElement("div");
			row.className = "opacity-row";
			const label = document.createElement("label");
			const value = document.createElement("span");
			const show = (v) => (value.textContent = Number(v) === 0 ? "none" : v + "px");
			label.append(widthLabels[slot] + " ", value);
			const input = document.createElement("input");
			input.type = "range";
			input.className = "custom-range";
			input.min = "0";
			input.max = "12";
			input.step = "0.5";
			input.value = String(widths[slot] == null ? 0 : widths[slot]);
			show(input.value);
			input.addEventListener("input", () => {
				layout().widths[slot] = Number(input.value);
				show(input.value);
				save();
			});
			row.append(label, input);
			els.colors.appendChild(row);
		}
	}

	function renderOptions() {
		els.options.replaceChildren();
		const opts = layout().options || {};
		const help = {
			card_background: "Card backgrounds (difficulty colour)",
			collapse_badges: "Combo badge takes the sync slot when there's no sync",
			count_pill_background: "Blue count pills",
			compact_lists: "Short lists pull later elements up",
		};
		for (const key of Object.keys(opts)) {
			const label = document.createElement("label");
			const box = document.createElement("input");
			box.type = "checkbox";
			box.checked = !!opts[key];
			box.addEventListener("change", () => {
				opts[key] = box.checked;
				changed();
			});
			label.append(box, " " + (help[key] || key));
			els.options.appendChild(label);
		}
	}

	function switchKind(kind) {
		state.kind = kind;
		els.kind.value = kind;
		if (!state.layouts[kind]) state.layouts[kind] = mergeLayout(kind, loadSaved(kind));
		state.view = "page";
		state.selected = null;
		state.sel.clear();
		resetHistory();
		els.viewToggle.classList.toggle("d-none", !KINDS[kind].defaults.card);
		for (const b of els.viewToggle.querySelectorAll("button")) b.classList.toggle("active", b.dataset.view === "page");
		renderOptions();
		renderColors();
		renderStage();
	}

	function init() {
		if (!KINDS) {
			setStatus("Couldn't load template-layouts.js.");
			return;
		}
		for (const [key, k] of Object.entries(KINDS)) els.kind.appendChild(new Option(k.title, key));
		els.kind.addEventListener("change", () => switchKind(els.kind.value));
		els.viewToggle.addEventListener("click", (ev) => {
			const btn = ev.target.closest("button[data-view]");
			if (!btn) return;
			state.view = btn.dataset.view;
			state.selected = null;
		state.sel.clear();
			for (const b of els.viewToggle.querySelectorAll("button")) b.classList.toggle("active", b === btn);
			renderStage();
		});
		for (const btn of document.querySelectorAll("[data-zoom]")) {
			btn.addEventListener("click", () => {
				state.zoom = btn.dataset.zoom;
				for (const b of document.querySelectorAll("[data-zoom]")) b.classList.toggle("active", b === btn);
				renderStage();
			});
		}
		els.baseInput.addEventListener("change", () => loadImage("base", els.baseInput.files[0]));
		els.topInput.addEventListener("change", () => loadImage("top", els.topInput.files[0]));
		els.baseClear.addEventListener("click", () => {
			els.baseInput.value = "";
			loadImage("base", null);
		});
		els.topClear.addEventListener("click", () => {
			els.topInput.value = "";
			loadImage("top", null);
		});
		for (const btn of document.querySelectorAll(".sb-tabs [data-tab]")) btn.addEventListener("click", () => setTab(btn.dataset.tab));
		els.modeEdit.addEventListener("click", () => setLive(false));
		els.modeLive.addEventListener("click", () => setLive(true));
		els.undoBtn.addEventListener("click", () => stepHistory(-1));
		els.redoBtn.addEventListener("click", () => stepHistory(1));
		els.guideBtn.addEventListener("click", () => setTab("guide"));
		els.welcomeGuide.addEventListener("click", () => {
			setTab("guide");
			dismissWelcome();
		});
		els.welcomeDismiss.addEventListener("click", dismissWelcome);
		els.filter.addEventListener("input", renderElements);
		// drop a layout.json (import) or an image (base; Shift for top) on the canvas
		els.scroll.addEventListener("dragover", (ev) => {
			ev.preventDefault();
			els.scroll.classList.add("drop");
		});
		els.scroll.addEventListener("dragleave", () => els.scroll.classList.remove("drop"));
		els.scroll.addEventListener("drop", (ev) => {
			ev.preventDefault();
			els.scroll.classList.remove("drop");
			const file = ev.dataTransfer.files[0];
			if (!file) return;
			if (file.type.startsWith("image/")) loadImage(ev.shiftKey ? "top" : "base", file);
			else if (/\.json$/i.test(file.name)) importLayout(file);
			else setStatus("Drop a PNG/JPEG/WebP image or a layout.json.");
		});
		for (const box of [els.showBase, els.showTop, els.showLabels]) box.addEventListener("change", renderStage);
		els.selectAll.addEventListener("click", () => {
			state.sel = new Set(Object.keys(labels()));
			state.selected = [...state.sel].pop();
			renderElements();
		});
		els.selectNone.addEventListener("click", () => select(null));
		els.exportBtn.addEventListener("click", exportLayout);
		els.importInput.addEventListener("change", () => {
			if (els.importInput.files[0]) importLayout(els.importInput.files[0]);
			els.importInput.value = "";
		});
		els.resetBtn.addEventListener("click", () => {
			if (!window.confirm(`Reset the ${spec().title} layout to the defaults?`)) return;
			state.layouts[state.kind] = clone(spec().defaults);
			state.selected = null;
			state.sel.clear();
			if (history[state.kind]) history[state.kind].at = 0;
			renderOptions();
			renderColors();
			changed();
			setStatus("Reset to defaults.");
		});
		for (const input of [els.fx, els.fy, els.fw, els.fh, els.fGap]) input.addEventListener("change", onField);
		for (const input of [els.fVisible, els.fFollow, els.fColor, els.fOutlineColor]) input.addEventListener("change", onField);
		for (const input of [els.fOpacity, els.fOutlineWidth]) input.addEventListener("input", onField);
		for (const [btn, key] of [[els.fColorReset, "color"], [els.fOutlineColorReset, "outline_color"], [els.fOutlineWidthReset, "outline_width"]]) {
			btn.addEventListener("click", () => {
				for (const t of selectedEls()) delete t[key];
				changed();
			});
		}
		els.stage.addEventListener("pointerdown", (ev) => {
			if (ev.target === els.stage || ev.target.parentElement === els.stage) select(null);
		});
		document.addEventListener("keydown", onKey);
		window.addEventListener("resize", () => {
			if (state.zoom === "fit") renderStage();
		});
		const ui = loadUi();
		if (!ui.welcomed) els.welcome.classList.remove("d-none");
		switchKind(state.kind);
		if (ui.live) setLive(true);
	}

	init();
})();
