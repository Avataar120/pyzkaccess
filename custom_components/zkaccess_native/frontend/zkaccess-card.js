// ZKAccess Native: dashboard card showing the access activity (same rows as the panel's
// Activity tab), updated live.
//
//   type: custom:zkaccess-activity-card
//   title: Front door          # optional
//   limit: 10                  # rows shown (1-100), default 10
//   filter: all                # all | open | actions | denied
//   person: 12                 # optional: only this person number
//   device: <config entry id>  # optional: when several devices are set up
// Date/time options following the user's Home Assistant profile: 12/24 h (time_format
// "language" / "system" / "12" / "24", same rule as the HA frontend) and time zone ("server" =
// the HA server's zone, "local" = the browser's).
const haTimeOpts = (hass) => {
  const loc = hass?.locale || {};
  const lang = loc.language || hass?.language || undefined;
  let hour12;
  if (loc.time_format === "12" || loc.time_format === "24") hour12 = loc.time_format === "12";
  else hour12 = new Date(2023, 0, 1, 22).toLocaleString(loc.time_format === "system" ? undefined : lang).includes("10");
  const opts = { hour12 };
  if (loc.time_zone === "server" && hass?.config?.time_zone) opts.timeZone = hass.config.time_zone;
  return [lang, opts];
};
const DOMAIN = "zkaccess_native";

const TEXTS = {
  en: {
    title: "Access activity", never: "No access yet", person_no: "Person #{n}", unknown_user: "UserID {u}",
    unknown_card: "Unknown card", second_presentation: "second presentation", no_device: "No ZKAccess device set up",
    res_granted: "Opened", res_action: "Action", res_denied: "Denied", res_other: "Device",
    method_fingerprint: "fingerprint", method_card: "card", method_code: "keypad code", method_remote: "remote",
    method_phone: "phone", method_other: "other",
  },
  fr: {
    title: "Activité du contrôle d'accès", never: "Aucun accès pour l'instant", person_no: "Personne n° {n}",
    unknown_user: "UserID {u}", unknown_card: "Carte inconnue", second_presentation: "second passage",
    no_device: "Aucun appareil ZKAccess configuré",
    res_granted: "Ouvert", res_action: "Action", res_denied: "Refusé", res_other: "Lecteur",
    method_fingerprint: "empreinte", method_card: "carte", method_code: "code clavier", method_remote: "à distance",
    method_phone: "téléphone", method_other: "autre",
  },
};
const EVENT_NAMES_FR = { "unregistered card": "carte inconnue", "unregistered fingerprint": "doigt inconnu",
  "access denied": "accès refusé", "phone refused": "téléphone refusé", "illegal time zone": "hors plage horaire",
  "anti passback": "anti-passback", "remote opening": "ouverture à distance", "cancel alarm": "alarme annulée" };
const STYLE = { granted: ["#e7f6ee", "#067647", "✓"], action: ["#e8eefc", "#1e40af", "★"],
  denied: ["#fdecea", "#b42318", "✕"], other: ["#f1f4f7", "#52606d", "↗"] };
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

class ZkAccessActivityCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._history = [];
    this._error = null;
  }

  static getStubConfig() { return { limit: 10 }; }

  setConfig(config) {
    const limit = Number(config.limit ?? 10);
    if (!(limit >= 1 && limit <= 100)) throw new Error("limit must be between 1 and 100");
    if (config.filter && !["all", "open", "actions", "denied"].includes(config.filter)) {
      throw new Error("filter must be all, open, actions or denied");
    }
    this._config = { ...config, limit };
    if (this._hass) this._restart();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first && this._config) this._restart();
  }

  getCardSize() { return 1 + Math.min(this._config?.limit ?? 10, 10); }

  connectedCallback() { if (this._hass && this._config && !this._unsub) this._restart(); }

  disconnectedCallback() { this._stop(); }

  lang() { return (this._hass?.locale?.language || this._hass?.language || "en").startsWith("fr") ? "fr" : "en"; }

  t(key, vars = {}) {
    let s = TEXTS[this.lang()][key] ?? TEXTS.en[key] ?? key;
    for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, v);
    return s;
  }

  eventName(name) {
    if (!name || this.lang() !== "fr") return name;
    const fr = EVENT_NAMES_FR[String(name).toLowerCase()];
    return fr ? fr.charAt(0).toUpperCase() + fr.slice(1) : name;
  }

  _stop() {
    if (this._unsub) { this._unsub.then((u) => u()).catch(() => {}); this._unsub = null; }
  }

  async _restart() {
    this._stop();
    const conn = this._hass.connection;
    try {
      let entry = this._config.device;
      if (!entry) {
        const devices = await conn.sendMessagePromise({ type: `${DOMAIN}/devices` });
        entry = devices[0]?.entry_id;
      }
      if (!entry) { this._error = this.t("no_device"); this.render(); return; }
      this._entry = entry;
      this._history = await conn.sendMessagePromise({ type: `${DOMAIN}/history`, entry_id: entry, limit: 300 });
      this._error = null;
      this._unsub = conn.subscribeMessage((msg) => {
        if (msg.kind !== "event") return;
        this._history.unshift(msg.event);
        this._history.length = Math.min(this._history.length, 300);
        this.render();
      }, { type: `${DOMAIN}/subscribe`, entry_id: entry });
    } catch (e) {
      this._error = e.message || String(e);
    }
    this.render();
  }

  fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const [lang, o] = haTimeOpts(this._hass);
    return isNaN(d) ? iso : d.toLocaleString(lang,
      { ...o, weekday: "short", hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
  }

  render() {
    const c = this._config || {};
    const f = c.filter || "all";
    const items = this._history.filter((h) => (f === "all" || (f === "open" && h.result === "granted")
      || (f === "actions" && h.result === "action") || (f === "denied" && h.result === "denied"))
      && (c.person == null || String(h.person) === String(c.person))).slice(0, c.limit);
    const rows = items.map((h) => {
      const [bg, fg, icon] = STYLE[h.result] || STYLE.other;
      const who = h.person_name || (h.user_id ? (h.person ? this.t("person_no", { n: h.person }) : this.t("unknown_user", { u: h.user_id }))
        : h.card ? this.t("unknown_card") : this.eventName(h.event_name));
      const parts = [h.category, h.method ? this.t("method_" + h.method) : null, h.phone, h.card && !h.person ? `#${h.card}` : null,
        h.gesture === "double" ? this.t("second_presentation") : null,
        (h.result === "denied" || h.result === "other") && this.eventName(h.event_name) !== who ? this.eventName(h.event_name) : null].filter(Boolean);
      const badge = h.result === "action" ? (h.category || this.t("res_action")) : this.t("res_" + (h.result || "other"));
      return `<div class="ev"><span class="time">${esc(this.fmtTime(h.time))}</span>
        <span class="dot" style="background:${bg};color:${fg}">${icon}</span>
        <div class="txt"><div class="who">${esc(who)}</div><div class="sub">${esc(parts.join(" · "))}</div></div>
        <span class="pill" style="background:${bg};color:${fg}">${esc(badge)}</span></div>`;
    }).join("");
    const body = this._error ? `<div class="ev sub">${esc(this._error)}</div>`
      : rows || `<div class="ev sub">${this.t("never")}</div>`;
    const title = c.title ?? this.t("title");
    this.shadowRoot.innerHTML = `<style>
      ha-card { overflow: hidden; }
      .head { padding: 16px 16px 8px; font-size: 20px; font-weight: 400; color: var(--primary-text-color); }
      .ev { display: flex; align-items: center; gap: 12px; padding: 10px 16px; border-top: 1px solid var(--divider-color, #e5e7eb); }
      .time { width: 88px; flex: none; font-size: 12px; color: var(--secondary-text-color); }
      .dot { width: 30px; height: 30px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-weight: 700; flex: none; }
      .txt { flex: 1; min-width: 0; }
      .who { font-weight: 500; color: var(--primary-text-color); }
      .sub { font-size: 13px; color: var(--secondary-text-color); overflow-wrap: anywhere; }
      .pill { border-radius: 999px; padding: 3px 10px; font-size: 12px; font-weight: 500; white-space: nowrap; flex: none; }
      @media (max-width: 420px) { .time { width: 64px; } .pill { display: none; } }
    </style><ha-card>${title ? `<div class="head">${esc(title)}</div>` : ""}${body}</ha-card>`;
  }
}

if (!customElements.get("zkaccess-activity-card")) {
  customElements.define("zkaccess-activity-card", ZkAccessActivityCard);
  window.customCards = window.customCards || [];
  window.customCards.push({ type: "zkaccess-activity-card", name: "ZKAccess activity",
    description: "Access activity of a ZKAccess Native device (people, category, fingerprint / card / phone), live." });
}
