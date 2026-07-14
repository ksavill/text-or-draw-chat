"use strict";

/* ================= constants ================= */
const CW = 228, CH = 80;            // compose canvas, native DS-ish resolution
const LINE = 16;                    // text rows are 16px tall
const ROWS = CH / LINE;             // 5 rows, like PictoChat
const MARGIN_X = 2;
const SCALE = 2;                    // canvas is displayed at 2x
const ROOMS = ["A", "B", "C", "D"];
const MAX_FEED = 120;
const VS15 = String.fromCharCode(0xFE0E);   // text-presentation selector

/* the 16 Nintendo DS profile colours (approximated) */
const PALETTE = [
    "#a8a8a8", "#b06830", "#e00028", "#ff78a8",
    "#ff8820", "#f0d020", "#a8d820", "#48c828",
    "#18a068", "#20c8a0", "#38b8f0", "#3878e0",
    "#2038c8", "#8048c8", "#b880e8", "#e858b8"
];

/* ================= state ================= */
let websocket = null;
let nickname = "";
let room = "";
let inRoom = false;
let entries = [];                    // typed glyphs (for backspace)
let cursor = { row: 0, x: MARGIN_X };
let row0StartX = MARGIN_X;           // first row starts right of the name tab
let inkDirty = false;
let shift = false, caps = false;
let erasing = false, penThick = false;
let lastSent = null;                 // {src, entries, cursor} of our last message
let unreadMessages = 0;
const originalTitle = document.title;
const glyphCache = {};

const $ = id => document.getElementById(id);
const canvas = $("canvas");
const ctx = canvas.getContext("2d", { willReadFrequently: false });
const feedEl = $("feed");

/* ================= colours ================= */
const userColors = {};   // name -> palette index, learned from server events

function colorOf(user) {
    if (userColors[user] !== undefined) return PALETTE[userColors[user] % PALETTE.length];
    let h = 0;   // fallback for servers that don't assign colours
    for (let i = 0; i < user.length; i++) h = (h * 31 + user.charCodeAt(i)) >>> 0;
    return PALETTE[h % PALETTE.length];
}

function tintOf(color) {   // pale fill for name tabs, like the DS
    return `color-mix(in srgb, ${color} 22%, #ffffff)`;
}

function applyMyColor() {
    const myColor = colorOf(nickname);
    $("box-tab").style.borderColor = myColor;
    $("box-tab").style.background = tintOf(myColor);
    $("canvas-holder").style.borderColor = myColor;
    $("rail-room").style.borderColor = myColor;
}

/* ================= glyph rendering =================
   Characters are rasterised once through an offscreen canvas
   (using the pixel font) and stamped onto the message canvas
   as crisp 1x pixels — text and ink share one bitmap, exactly
   like PictoChat. Advance widths are measured per glyph, so
   text is variable-width like the DS font. */
function renderGlyph(ch) {
    if (glyphCache[ch]) return glyphCache[ch];
    if (ch === " ") return (glyphCache[ch] = { px: [], adv: 4, wide: false });
    const wide = ch.charCodeAt(0) > 0x2000;   // kana / pictograms
    const w = wide ? 16 : 10, h = wide ? 16 : 12;
    const g = document.createElement("canvas");
    g.width = w; g.height = h;
    const gx = g.getContext("2d", { willReadFrequently: true });
    gx.fillStyle = "#000";
    if (wide) {
        gx.font = '12px "MS Gothic", "Yu Gothic", monospace';
        gx.textAlign = "center"; gx.textBaseline = "middle";
        gx.fillText(ch + String.fromCharCode(0xFE0E), 8, 9);   // VS-15: monochrome
    } else {
        gx.font = '8px "Press Start 2P", monospace';
        gx.textAlign = "left"; gx.textBaseline = "alphabetic";
        gx.fillText(ch, 0, 8);
    }
    const data = gx.getImageData(0, 0, w, h).data;
    const px = [];
    let maxX = -1;
    for (let y = 0; y < h; y++)
        for (let x = 0; x < w; x++)
            if (data[(y * w + x) * 4 + 3] > 100) {
                px.push([x, y]);
                if (x > maxX) maxX = x;
            }
    const glyph = { px, adv: maxX >= 0 ? maxX + 2 : 4, wide };
    glyphCache[ch] = glyph;
    return glyph;
}

function stampChar(ch) {
    const glyph = renderGlyph(ch);
    if (cursor.x + glyph.adv > CW - MARGIN_X) {
        if (cursor.row >= ROWS - 1) return false;   // box is full
        cursor.row++; cursor.x = MARGIN_X;
    }
    const x0 = cursor.x;
    const y0 = cursor.row * LINE + (glyph.wide ? 0 : 2);
    ctx.fillStyle = "#000";
    for (const [x, y] of glyph.px) ctx.fillRect(x0 + x, y0 + y, 1, 1);
    entries.push({ ch, nl: false, row: cursor.row, x: cursor.x, adv: glyph.adv });
    cursor.x += glyph.adv;
    updateCaret();
    return true;
}

function newlineChar() {
    if (cursor.row >= ROWS - 1) return;
    entries.push({ ch: "\n", nl: true, row: cursor.row, x: cursor.x });
    cursor.row++; cursor.x = MARGIN_X;
    updateCaret();
}

function backspaceChar() {
    const e = entries.pop();
    if (!e) return;
    if (!e.nl) ctx.clearRect(e.x, e.row * LINE, e.adv, LINE);
    cursor = { row: e.row, x: e.x };
    updateCaret();
}

function updateCaret() {
    const caret = $("caret");
    if (cursor.row >= ROWS) { caret.style.display = "none"; return; }
    caret.style.display = "block";
    caret.style.left = (cursor.x * SCALE) + "px";
    caret.style.top = (cursor.row * LINE * SCALE + 3) + "px";
}

/* ================= pen / eraser =================
   Strokes are painted as square 1x pixels (no antialiasing)
   for the chunky DS look. The eraser clears to transparent,
   so it removes stamped text too — same bitmap, same rules. */
let strokeActive = false, lastPos = null;

function penSizePx() { return erasing ? 6 : (penThick ? 3 : 1); }

function canvasPos(e) {
    const r = canvas.getBoundingClientRect();
    return {
        x: (e.clientX - r.left) / r.width * CW,
        y: (e.clientY - r.top) / r.height * CH
    };
}
function paintDot(p) {
    const s = penSizePx();
    const x = Math.round(p.x - s / 2), y = Math.round(p.y - s / 2);
    if (erasing) ctx.clearRect(x, y, s, s);
    else { ctx.fillStyle = "#000"; ctx.fillRect(x, y, s, s); inkDirty = true; }
}
function paintLine(a, b) {
    const steps = Math.max(Math.abs(b.x - a.x), Math.abs(b.y - a.y), 1);
    for (let i = 0; i <= steps; i++)
        paintDot({ x: a.x + (b.x - a.x) * i / steps, y: a.y + (b.y - a.y) * i / steps });
}
function movePenCursor(e) {
    const r = canvas.getBoundingClientRect();
    const pc = $("pen-cursor");
    const s = Math.max(3, penSizePx() * SCALE);
    pc.style.display = "block";
    pc.style.width = s + "px"; pc.style.height = s + "px";
    pc.style.left = (e.clientX - r.left) + "px";
    pc.style.top = (e.clientY - r.top) + "px";
}

canvas.addEventListener("pointerdown", e => {
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    strokeActive = true;
    lastPos = canvasPos(e);
    paintDot(lastPos);
});
canvas.addEventListener("pointermove", e => {
    movePenCursor(e);
    if (!strokeActive) return;
    const p = canvasPos(e);
    paintLine(lastPos, p);
    lastPos = p;
});
canvas.addEventListener("pointerleave", () => { $("pen-cursor").style.display = "none"; });
const endStroke = () => { strokeActive = false; lastPos = null; };
canvas.addEventListener("pointerup", endStroke);
canvas.addEventListener("pointercancel", endStroke);

/* ================= compose actions ================= */
function clearCompose() {
    ctx.clearRect(0, 0, CW, CH);
    entries = [];
    cursor = { row: 0, x: row0StartX };
    inkDirty = false;
    updateCaret();
}

function sendMessage() {
    if (!websocket || websocket.readyState !== WebSocket.OPEN) return;
    if (!inkDirty && entries.length === 0) return;
    const src = canvas.toDataURL();
    lastSent = {
        src,
        entries: entries.map(e => ({ ...e })),
        cursor: { ...cursor }
    };
    websocket.send(JSON.stringify({ type: "drawing", data: src }));
    clearCompose();
    playSound("send-sound");
}

function copyNewest() {
    const inks = feedEl.querySelectorAll("img.ink");
    if (!inks.length) return;
    const src = inks[inks.length - 1].src;
    const img = new Image();
    img.onload = () => {
        clearCompose();
        ctx.drawImage(img, 0, 0, CW, CH);
        inkDirty = true;
        if (lastSent && src === lastSent.src) {
            // copying our own last message: typed text stays editable
            entries = lastSent.entries.map(e => ({ ...e }));
            cursor = { ...lastSent.cursor };
            updateCaret();
        }
    };
    img.src = src;
}

$("send-btn").addEventListener("click", sendMessage);
$("clear-btn").addEventListener("click", clearCompose);
$("copy-btn").addEventListener("click", copyNewest);
$("pen-btn").addEventListener("click", () => setEraser(false));
$("erase-btn").addEventListener("click", () => setEraser(true));
$("size-btn").addEventListener("click", () => {
    penThick = !penThick;
    $("size-btn").textContent = penThick ? "●" : "•";
});
function setEraser(on) {
    erasing = on;
    $("pen-btn").classList.toggle("on", !on);
    $("erase-btn").classList.toggle("on", on);
}

/* ================= on-screen keyboard ================= */
const KEYBOARDS = [
    { id: "alnum", label: "A1", rows: [
        ["1","2","3","4","5","6","7","8","9","0","-","="],
        ["q","w","e","r","t","y","u","i","o","p","⌫"],
        ["CAP","a","s","d","f","g","h","j","k","l","↵"],
        ["SHIFT","z","x","c","v","b","n","m",",",".","/"],
        ["'","\"","SPACE","!","?"]
    ]},
    { id: "accent", label: "É", rows: [
        ["à","á","â","ä","è","é","ê","ë","ì","í","⌫"],
        ["î","ï","ò","ó","ô","ö","ù","ú","û","ü","↵"],
        ["ñ","ç","ß","ã","õ","ø","å","æ","¡","¿"],
        ["À","Á","Ä","È","É","Ì","Í","Ò","Ó","Ù"],
        ["Ñ","Ç","SPACE","Ö","Ü"]
    ]},
    { id: "symbol", label: "!?", rows: [
        ["!","?","&","%","+","-","×","÷","=","@","⌫"],
        ["#","$","~","^","_","|","\\","/",":",";","↵"],
        ["(",")","[","]","{","}","<",">","\"","'","*"],
        ["¢","£","¥","§","°","¤","µ","¬","¦","·"],
        [",",".","SPACE","…","‥"]
    ]},
    { id: "kana", label: "か", rows: [
        ["あ","い","う","え","お","か","き","く","け","こ","⌫"],
        ["さ","し","す","せ","そ","た","ち","つ","て","と","↵"],
        ["な","に","ぬ","ね","の","は","ひ","ふ","へ","ほ","ー"],
        ["ま","み","む","め","も","や","ゆ","よ","ら","り","っ"],
        ["る","れ","ろ","わ","を","ん","゛","゜","SPACE"]
    ]},
    /* modelled on the real DS pictograph tab: faces, weather,
       objects, card suits, 8-way arrows, checkboxes */
    { id: "picto", label: "☺", rows: [
        ["☺","☻","☹","☽","☀","☁","☂","☃","✉","☎","⌫"],
        ["⌚","♠","♥","♦","♣","♡","♢","★","☆","↵"],
        ["←","→","↑","↓","↖","↗","↘","↙","☒","☐"],
        ["♪","♫","⚡","✚","✖","●","○","■","□","◎"],
        ["▲","▼","SPACE","◆","◇"]
    ]}
];
let activeKb = 0;

/* dakuten / handakuten combine with the previous kana, like an IME */
const DAKUTEN = {
    "か":"が","き":"ぎ","く":"ぐ","け":"げ","こ":"ご",
    "さ":"ざ","し":"じ","す":"ず","せ":"ぜ","そ":"ぞ",
    "た":"だ","ち":"ぢ","つ":"づ","て":"で","と":"ど",
    "は":"ば","ひ":"び","ふ":"ぶ","へ":"べ","ほ":"ぼ","う":"ゔ"
};
const HANDAKUTEN = { "は":"ぱ","ひ":"ぴ","ふ":"ぷ","へ":"ぺ","ほ":"ぽ" };

function buildKbTabs() {
    const tabs = $("kb-tabs");
    tabs.innerHTML = "";
    KEYBOARDS.forEach((kb, i) => {
        const b = document.createElement("button");
        b.className = "kb-tab" + (i === activeKb ? " active" : "");
        b.textContent = kb.label.length === 1 && kb.label.charCodeAt(0) > 0x2000
            ? kb.label + VS15 : kb.label;
        b.addEventListener("click", () => { activeKb = i; shift = false; buildKbTabs(); buildKbKeys(); });
        tabs.appendChild(b);
    });
}

function keyLabel(k) {
    if (k.length === 1 && k >= "a" && k <= "z" && (shift !== caps)) return k.toUpperCase();
    if (k.length === 1 && k.charCodeAt(0) > 0x2000) return k + VS15;
    return k;
}

function buildKbKeys() {
    const holder = $("kb-keys");
    holder.innerHTML = "";
    KEYBOARDS[activeKb].rows.forEach(row => {
        const rowEl = document.createElement("div");
        rowEl.className = "kb-row";
        row.forEach(k => {
            const b = document.createElement("button");
            b.className = "key";
            if (k === "SPACE") { b.classList.add("special", "space"); b.textContent = "SPACE"; }
            else if (k === "⌫") { b.classList.add("special", "wide"); b.textContent = "⌫"; }
            else if (k === "↵") { b.classList.add("enter", "wide"); b.textContent = "↵"; }
            else if (k === "SHIFT" || k === "CAP") {
                b.classList.add("special", "wide");
                b.textContent = k;
                if (k === "SHIFT" && shift) b.classList.add("on");
                if (k === "CAP" && caps) b.classList.add("on");
            }
            else b.textContent = keyLabel(k);
            b.addEventListener("click", () => pressKey(k));
            rowEl.appendChild(b);
        });
        holder.appendChild(rowEl);
    });
}

function pressKey(k) {
    if (k === "SHIFT") { shift = !shift; buildKbKeys(); return; }
    if (k === "CAP") { caps = !caps; buildKbKeys(); return; }
    if (k === "⌫") { backspaceChar(); return; }
    if (k === "↵") { newlineChar(); return; }
    if (k === "SPACE") { stampChar(" "); return; }
    if (k === "゛" || k === "゜") {
        const map = k === "゛" ? DAKUTEN : HANDAKUTEN;
        const last = entries[entries.length - 1];
        if (last && !last.nl && map[last.ch]) {
            backspaceChar();
            stampChar(map[last.ch]);
            return;
        }
    }
    let ch = k;
    if (ch.length === 1 && ch >= "a" && ch <= "z" && (shift !== caps)) ch = ch.toUpperCase();
    stampChar(ch);
    if (shift) { shift = false; buildKbKeys(); }
}

/* physical keyboard types straight into the box */
document.addEventListener("keydown", e => {
    if (!inRoom) return;
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
    if (e.ctrlKey || e.metaKey || e.altKey) {
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { sendMessage(); e.preventDefault(); }
        return;
    }
    if (e.key === "Enter") {
        if (e.shiftKey) newlineChar(); else sendMessage();
        e.preventDefault();
    } else if (e.key === "Backspace") {
        backspaceChar();
        e.preventDefault();
    } else if (e.key.length === 1) {
        stampChar(e.key);
        e.preventDefault();
    }
});

/* ================= message feed ================= */
function feedAppend(node) {
    const atBottom = feedEl.scrollHeight - feedEl.clientHeight - feedEl.scrollTop < 6;
    feedEl.appendChild(node);
    while (feedEl.children.length > MAX_FEED) feedEl.firstChild.remove();
    if (atBottom) feedEl.scrollTop = feedEl.scrollHeight;
}

function makeBubble(sender, contentNode) {
    const color = colorOf(sender);
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.style.borderColor = color;
    const tab = document.createElement("span");
    tab.className = "tab";
    tab.textContent = sender;
    tab.style.borderColor = color;
    tab.style.background = tintOf(color);
    bubble.appendChild(tab);
    bubble.appendChild(contentNode);
    return bubble;
}

function addDrawingMessage(sender, dataUrl) {
    const img = new Image();
    img.className = "ink";
    img.src = dataUrl;
    feedAppend(makeBubble(sender, img));
}

function addTextMessage(sender, text) {
    const div = document.createElement("div");
    div.className = "txt";
    div.textContent = text;
    feedAppend(makeBubble(sender, div));
}

function addSystemLine(text) {
    const div = document.createElement("div");
    div.className = "sysline";
    div.textContent = text;
    feedAppend(div);
}

function setTicker(text) {
    $("ticker-text").textContent = text;
}

/* ================= sounds / title ================= */
function playSound(id) {
    const a = $(id);
    if (a) { a.currentTime = 0; a.play().catch(() => {}); }
}
document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
        unreadMessages = 0;
        document.title = originalTitle;
    }
});
function noteUnread() {
    if (document.hidden) {
        unreadMessages++;
        document.title = `(${unreadMessages}) PictoChat`;
    }
}

/* ================= rooms / websocket ================= */
function wsUrl(r, n) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    return `${proto}://${location.host}/ws/${encodeURIComponent(r)}/${encodeURIComponent(n)}`;
}

function joinRoom(selectedRoom) {
    const name = $("nickname").value.trim();
    if (!name) {
        $("join-hint").textContent = "Please enter a name first.";
        $("nickname").focus();
        return;
    }
    $("join-hint").textContent = "";
    nickname = name;
    room = selectedRoom;

    if (websocket) websocket.close();
    websocket = new WebSocket(wsUrl(room, nickname));

    websocket.onopen = () => {
        inRoom = true;
        feedEl.innerHTML = "";
        Object.keys(userColors).forEach(k => delete userColors[k]);
        $("logo-view").style.display = "none";
        $("feed-view").style.display = "flex";
        $("select-view").style.display = "none";
        $("chat-view").style.display = "flex";
        $("rail-room").textContent = room;
        $("room-badge").textContent = `ROOM ${room}`;
        $("box-tab").textContent = nickname;
        applyMyColor();   // hash fallback now; corrected by our join event
        // first text row starts to the right of the name tab, like the DS
        row0StartX = Math.min(CW - 8,
            Math.ceil(($("box-tab").offsetLeft + $("box-tab").offsetWidth) / SCALE) + 2);
        clearCompose();
        setEraser(false);
        setTicker(`Now entering Chat Room ${room}...`);
        addSystemLine(`Now entering Chat Room ${room}...`);
    };

    websocket.onmessage = event => {
        let parsed = null;
        try { parsed = JSON.parse(event.data); } catch (err) { /* legacy plain text */ }
        if (parsed && parsed.type === "system") {
            if (parsed.color !== undefined && parsed.name) userColors[parsed.name] = parsed.color;
            if (parsed.users !== undefined) $("room-badge").textContent = `ROOM ${room} · ${parsed.users}/16`;
            // PictoChat shows join/leave as text only — no sound
            if (parsed.event === "join") {
                addSystemLine(`${parsed.name} has joined the chat.`);
                setTicker(`▣ ${parsed.name} has joined the chat.`);
                if (parsed.name === nickname) applyMyColor();
            } else if (parsed.event === "leave") {
                addSystemLine(`${parsed.name} has left the chat.`);
                setTicker(`▣ ${parsed.name} has left the chat.`);
                delete userColors[parsed.name];
            }
        } else if (parsed && parsed.type === "error") {
            addSystemLine(parsed.reason || "Error.");
        } else if (parsed && parsed.type === "drawing") {
            if (parsed.color !== undefined) userColors[parsed.sender] = parsed.color;
            addDrawingMessage(parsed.sender, parsed.data);
            if (parsed.sender !== nickname) playSound("ping-sound");
            noteUnread();
        } else if (parsed && parsed.type === "message") {
            if (parsed.color !== undefined) userColors[parsed.sender] = parsed.color;
            addTextMessage(parsed.sender, parsed.message);
            if (parsed.sender !== nickname) playSound("ping-sound");
            noteUnread();
        } else {
            addSystemLine(event.data);
        }
    };

    websocket.onclose = event => {
        inRoom = false;
        $("logo-view").style.display = "flex";
        $("feed-view").style.display = "none";
        $("select-view").style.display = "flex";
        $("chat-view").style.display = "none";
        $("room-badge").textContent = "";
        setTicker("Welcome to PictoChat.");
        // the server explains rejections (room full, name taken, ...)
        if (event.code >= 4000 && event.reason)
            $("join-hint").textContent = event.reason;
        unreadMessages = 0;
        document.title = originalTitle;
        refreshCounts();
    };

    websocket.onerror = () => {
        addSystemLine("Connection error.");
    };
}

$("exit-button").addEventListener("click", () => { if (websocket) websocket.close(); });
document.querySelectorAll(".room-row").forEach(btn => {
    btn.addEventListener("click", () => joinRoom(btn.dataset.room));
});
$("nickname").addEventListener("keydown", e => {
    if (e.key === "Enter") joinRoom("A");
});

/* room occupancy — polled for the select screen and the header badge */
async function refreshCounts() {
    for (const r of ROOMS) {
        try {
            const res = await fetch(`/users/${r}`);
            const data = await res.json();
            if (data.error) continue;
            const row = document.querySelector(`.room-row[data-room="${r}"]`);
            if (row) {
                row.disabled = data.users >= 16;
                row.querySelector(".room-count").textContent = `${data.users}/16`;
                const pips = row.querySelector(".room-pips");
                if (!pips.children.length)
                    for (let i = 0; i < 16; i++) {
                        const p = document.createElement("span");
                        p.className = "pip";
                        pips.appendChild(p);
                    }
                [...pips.children].forEach((p, i) => p.classList.toggle("on", i < data.users));
            }
            if (inRoom && r === room)
                $("room-badge").textContent = `ROOM ${room} · ${data.users}/16`;
        } catch (err) { /* server unreachable; try again next tick */ }
    }
}

async function refreshBridgeStatus() {
    const status = $("ds-bridge-status");
    try {
        const res = await fetch("/ds-bridge/status", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const online = data.available === true;
        status.dataset.state = online ? "online" : "offline";
        const rooms = Array.isArray(data.rooms) ? data.rooms.join(", ") : "";
        $("ds-bridge-label").textContent = online
            ? `DS HARDWARE: ONLINE · ROOM ${rooms || "B"}`
            : "DS HARDWARE: OFFLINE · ESP32-S3 + W5500 REQUIRED";
        status.title = online
            ? "The radio-connected DS bridge is ready."
            : "Stock DS communication requires the ESP32-S3 and W5500 bridge hardware.";
    } catch (err) {
        status.dataset.state = "checking";
        $("ds-bridge-label").textContent = "DS HARDWARE: STATUS UNKNOWN";
        status.title = "The server health endpoint could not be reached.";
    }
}

/* ================= fit the DS to the window ================= */
function fitDS() {
    const ds = $("ds");
    const w = document.documentElement.clientWidth;
    const base = w <= 600 ? 532 : 568;
    const s = Math.min(1, (w - 6) / base);
    ds.style.transform = s < 1 ? `scale(${s})` : "";
    ds.style.transformOrigin = "top center";
    // reclaim the layout height the scale leaves behind
    ds.style.marginBottom = s < 1 ? (-ds.offsetHeight * (1 - s)) + "px" : "";
}
window.addEventListener("resize", fitDS);

/* ================= init ================= */
document.addEventListener("DOMContentLoaded", () => {
    buildKbTabs();
    buildKbKeys();
    updateCaret();
    fitDS();
    refreshCounts();
    refreshBridgeStatus();
    setInterval(refreshCounts, 5000);
    setInterval(refreshBridgeStatus, 5000);
    if (document.fonts && document.fonts.load)
        document.fonts.load('8px "Press Start 2P"').then(() => {
            // drop any glyphs rasterised before the pixel font arrived
            for (const k of Object.keys(glyphCache)) delete glyphCache[k];
        });
    if ("serviceWorker" in navigator)
        navigator.serviceWorker.register("/sw.js").catch(() => {});
});
