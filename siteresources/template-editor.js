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
		selected: null,
		zoom: "fit",
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
		try {
			window.localStorage.setItem(STORAGE_PREFIX + state.kind, JSON.stringify(layout()));
		} catch (e) {
			// private mode / storage blocked - nothing to do
		}
	}

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
			node.classList.toggle("selected", state.selected === name);
			node.style.opacity = el.opacity == null ? "" : String(Math.max(0.15, el.opacity));
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
			item.classList.toggle("selected", state.selected === name);
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
			item.addEventListener("click", () => select(name));
			els.list.appendChild(item);
		});
		renderSelection();
	}

	function renderSelection() {
		const name = state.selected;
		const el = name && group()[name];
		if (!el) {
			els.selTitle.textContent = "No element selected";
			els.selHint.classList.remove("d-none");
			els.selFields.classList.add("d-none");
			return;
		}
		els.selTitle.textContent = labels()[name];
		els.selHint.classList.add("d-none");
		els.selFields.classList.remove("d-none");
		els.fx.value = effectiveX(name);
		els.fy.value = el.y;
		els.fw.value = el.w;
		els.fh.value = el.h;
		const mode = resizeMode(name);
		els.fw.disabled = mode === "none";
		els.fh.disabled = mode !== "both";
		els.resizeNote.textContent =
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

		const opacity = Math.round((el.opacity == null ? 1 : el.opacity) * 100);
		els.fOpacity.value = opacity;
		els.fOpacityVal.textContent = opacity + "%";
		els.fOpacity.disabled = name.startsWith("grid_"); // the bot ignores opacity on grids
		const text = isText(name);
		els.textStyle.classList.toggle("d-none", !text);
		if (text) {
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

	function select(name) {
		state.selected = name;
		renderElements();
	}

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
		if (state.selected !== name) select(name);
		const target = els.elementLayer.querySelector(`.el[data-name="${CSS.escape(name)}"]`);
		drag = {
			name,
			dir: ev.target.classList.contains("handle") ? ev.target.dataset.dir : "move",
			startX: ev.clientX,
			startY: ev.clientY,
			box: { x: effectiveX(name), y: el.y, w: el.w, h: el.h },
			follow: el.follow,
			moved: false,
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
			if (drag.follow && Math.abs(dx) >= 1) {
				el.follow = null; // a sideways drag pins it to a fixed x
			}
			if (!el.follow) el.x = snap(drag.box.x + dx);
			el.y = snap(drag.box.y + dy);
		} else {
			if (drag.dir.includes("e")) el.w = Math.max(MIN_SIZE, snap(drag.box.w + dx));
			if (drag.dir.includes("s")) el.h = Math.max(MIN_SIZE, snap(drag.box.h + dy));
		}
		// cheap live update of just this node while dragging
		const node = ev.currentTarget;
		Object.assign(node.style, {
			left: effectiveX(drag.name) + "px",
			top: el.y + "px",
			width: el.w + "px",
			height: el.h + "px",
		});
		node.classList.toggle("follows", !!el.follow);
		renderSelection();
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
		} else if (input === els.fVisible) {
			el.visible = input.checked;
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
		} else if (input === els.fOpacity) {
			const v = Math.min(100, Math.max(0, Number(input.value)));
			if (v >= 100) delete el.opacity; // absent = stock look
			else el.opacity = v / 100;
		} else if (input === els.fColor) {
			el.color = input.value;
		} else if (input === els.fOutlineColor) {
			el.outline_color = input.value;
		} else if (input === els.fOutlineWidth) {
			el.outline_width = Math.min(MAX_OUTLINE, Math.max(0, Number(input.value)));
		}
		changed();
	}

	function onKey(ev) {
		const tag = (ev.target.tagName || "").toLowerCase();
		if (tag === "input" || tag === "select" || tag === "textarea") return;
		if (ev.key === "Escape") {
			select(null);
			return;
		}
		const name = state.selected;
		const el = name && group()[name];
		const step = ev.shiftKey ? 10 : 1;
		const moves = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
		if (!el || !moves[ev.key]) return;
		ev.preventDefault();
		const [dx, dy] = moves[ev.key];
		if (dx) {
			el.x = effectiveX(name) + dx;
			el.follow = null;
		}
		el.y += dy;
		changed();
	}

	// ---- import / export

	function setStatus(text) {
		els.status.textContent = text;
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
			changed();
			renderOptions();
			setStatus(`Imported ${file.name}.`);
		};
		reader.readAsText(file);
	}

	function loadImage(which, input) {
		const file = input.files && input.files[0];
		if (state.images[which]) URL.revokeObjectURL(state.images[which]);
		state.images[which] = file ? URL.createObjectURL(file) : null;
		if (file) {
			const img = new Image();
			img.onload = () => {
				const [w, h] = spec().defaults.canvas;
				const note = spec().fixedSize && Math.abs(img.width / img.height - w / h) > 0.02 * (w / h)
					? ` Its aspect ratio doesn't match the ${w}×${h} canvas, so it will be stretched.`
					: "";
				setStatus(`${which} image: ${img.width}×${img.height}.${note}`);
			};
			img.src = state.images[which];
		}
		renderImages();
	}

	// ---- setup

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
		els.viewToggle.classList.toggle("d-none", !KINDS[kind].defaults.card);
		for (const b of els.viewToggle.querySelectorAll("button")) b.classList.toggle("active", b.dataset.view === "page");
		renderOptions();
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
		els.baseInput.addEventListener("change", () => loadImage("base", els.baseInput));
		els.topInput.addEventListener("change", () => loadImage("top", els.topInput));
		for (const box of [els.showBase, els.showTop, els.showLabels]) box.addEventListener("change", renderStage);
		els.exportBtn.addEventListener("click", exportLayout);
		els.importInput.addEventListener("change", () => {
			if (els.importInput.files[0]) importLayout(els.importInput.files[0]);
			els.importInput.value = "";
		});
		els.resetBtn.addEventListener("click", () => {
			if (!window.confirm(`Reset the ${spec().title} layout to the defaults?`)) return;
			state.layouts[state.kind] = clone(spec().defaults);
			state.selected = null;
			renderOptions();
			changed();
			setStatus("Reset to defaults.");
		});
		for (const input of [els.fx, els.fy, els.fw, els.fh, els.fGap]) input.addEventListener("change", onField);
		for (const input of [els.fVisible, els.fFollow, els.fColor, els.fOutlineColor]) input.addEventListener("change", onField);
		for (const input of [els.fOpacity, els.fOutlineWidth]) input.addEventListener("input", onField);
		for (const [btn, key] of [[els.fColorReset, "color"], [els.fOutlineColorReset, "outline_color"], [els.fOutlineWidthReset, "outline_width"]]) {
			btn.addEventListener("click", () => {
				const el = state.selected && group()[state.selected];
				if (!el) return;
				delete el[key];
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
		switchKind(state.kind);
	}

	init();
})();
