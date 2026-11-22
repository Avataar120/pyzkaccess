/*
 * ZKAccess Native: access control management panel for Home Assistant.
 *
 * One vanilla web component, no build step. It talks to the integration
 * through its admin-only WebSocket API (zkaccess_native/*).
 * Texts are in English by default; other languages come from I18N below.
 */

const DOMAIN = "zkaccess_native";

// Physical fingers, ZKTeco order: 0 = left little ... 4 = left thumb, 5 = right thumb ... 9 = right little.
const FINGER_KEYS = ["little", "ring", "middle", "index", "thumb", "thumb", "index", "middle", "ring", "little"];
const FINGER_HEIGHT = { little: 70, ring: 92, middle: 102, index: 94, thumb: 62 };
const CATEGORY_TYPES = ["open", "close", "lock", "unlock", "arm", "disarm", "custom"];
const PALETTE = ["#0f766e", "#1e40af", "#b45309", "#7c3aed", "#be185d", "#0369a1", "#4d7c0f", "#a16207", "#475569", "#9f1239"];

const I18N = {
  en: {
    title: "Access control", device: "Device", connected: "Connected", disconnected: "Disconnected",
    tab_people: "People", tab_activity: "Activity", tab_door: "Door", tab_settings: "Settings",
    people: "people", fingerprints: "fingerprints", cards: "cards", search: "Search a name or number",
    learn_card: "Learn a card", new_person: "+ New person", person: "Person", person_no: "Person #{n}",
    categories_col: "Categories (fingerprints)", card: "Card", code: "Code", last_access: "Last access",
    access: "Access", active: "Active", disabled: "Disabled", yes: "Yes", none: "—", never: "Never",
    unassigned: "Unassigned",
    unassigned_help: "UserIDs stored in the device that don't follow the “person + category” numbering (e.g. created on the keypad).",
    assign: "Assign to a person…", delete: "Delete", cancel: "Cancel", save: "Save", done: "Done", close: "Close",
    back: "← People", access_enabled: "Access enabled", fingerprints_title: "Fingerprints",
    fingers_help: "Each enrolled finger belongs to a category. Click a free finger to enroll it, or an enrolled finger to change its category or delete it.",
    left_hand: "Left hand", right_hand: "Right hand",
    finger_little: "Little", finger_ring: "Ring", finger_middle: "Middle", finger_index: "Index", finger_thumb: "Thumb",
    left: "Left", right: "Right", category: "Category", delete_fingerprint: "Delete fingerprint",
    unplaced: "Fingerprints not placed on the hand",
    unplaced_help: "Enrolled on the keypad: tell which finger it is so it shows on the hand.",
    place_on: "Finger", slot: "UserID {u} · slot {s}",
    categories_of: "Categories", user_id: "UserID", relay: "Device relay", relay_yes: "Yes", relay_no: "No (Home Assistant only)",
    creds_title: "Cards and keypad codes",
    creds_help: "One card and one code per category. On the keypad, the code is typed after the UserID, so the same code can be reused in every category.",
    replace: "Replace", remove: "Remove", set_code: "Set a code", change: "Change", code_set: "Set (••••)",
    identity: "Identity", name: "Name (stored in the device)", person_number: "Person number",
    ha_person: "Home Assistant person", no_ha_person: "— none —",
    recent: "Recent activity", delete_person: "Delete person",
    delete_person_help: "Also deletes the UserIDs {ids}, their fingerprints, cards, codes and access rights. A backup is made first.",
    delete_person_btn: "Delete {name}…",
    confirm_delete_person: "Delete {name} and all their credentials?",
    add_fingerprint: "Add a fingerprint", category_of_finger: "Category of this finger",
    no_relay_note: "“{c}” does not trigger the device relay: only Home Assistant reacts.",
    on_keypad: "On the device keypad, type:", admin_code: "admin code", add: "add",
    place_finger: "place the finger <b>3 times</b>", eq_person_cat: "= person <b>{p}</b> + category <b>{d} ({c})</b>",
    start: "Start", waiting: "Waiting for the new fingerprint on the device…", enrolled: "Fingerprint enrolled.",
    timeout: "No new fingerprint after 5 minutes. Try again.",
    card_wait: "Present the card to the reader…", card_detected: "Card detected", unknown_to_device: "Unknown to the device",
    enter_card: "…or type the card number", write_device: "Write to device",
    code_title: "Keypad code — {c}", code_label: "Code (1 to 6 digits)",
    new_person_title: "New person", number_auto: "Number (empty = next free)",
    assign_title: "Assign UserID {u}", existing_person: "Existing person", new_number: "New person number",
    filter_all: "All", filter_open: "Openings", filter_actions: "Actions", filter_denied: "Denied",
    everyone: "Everyone", live: "Live", assign_card: "Assign card…",
    res_granted: "Opened", res_action: "Action", res_denied: "Denied", res_other: "Device",
    method_fingerprint: "fingerprint", method_card: "card", method_code: "keypad code", method_remote: "remote", method_other: "other",
    second_presentation: "second presentation", unknown_card: "Unknown card", unknown_user: "UserID {u}",
    door_n: "Door {n} — lock relay", door_sensor: "Door sensor", relay_state: "Relay", alarm: "Alarm",
    not_wired: "Not wired", open_state: "Open", closed_state: "Closed", alarm_none: "None", alarm_on: "Alarm",
    open_for: "Open for", open_door: "Open door", hold_open: "Hold open", close_door: "Close",
    remote_note: "Remote openings show in the activity log. Automations can use the “Open door” button entity and the zkaccess_native.open_door action.",
    door_settings: "Door settings", written_device: "(written to the device)",
    relay_seconds: "Relay activation time (s)", sensor_type: "Door sensor", sensor_0: "None", sensor_1: "Normally open", sensor_2: "Normally closed",
    alarm_delay: "Alarm when left open for (s)", verify_mode: "Verification mode (device value)",
    free_passage: "Free-passage time zone (0 = none)", close_and_lock: "Relock as soon as the door closes",
    save_device: "Save to device", clock: "Device clock", sync_now: "Sync now", device_info: "Device",
    model: "Model", firmware: "Firmware", doors: "Doors", cancel_alarm: "Cancel alarm",
    cancel_alarm_help: "Stops the device alarm (tamper, door forced…).",
    action_categories: "Action categories",
    categories_help: "UserID = person number (2 digits) + category digit. E.g. person 12 + category 3 = UserID 123. Automations react to “category” in the access event.",
    second_help: "Second presentation: the category action runs at once; presenting the same card or finger again within the delay also runs the second category (gesture: double).",
    digit: "Digit", type: "Type", display_name: "Display name", second_pres: "Second presentation", used_by: "Used by",
    unused: "— unused —", type_open: "Open", type_close: "Close", type_lock: "Lock", type_unlock: "Unlock",
    type_arm: "Arm", type_disarm: "Disarm", type_custom: "Custom", fixed: "(fixed)", within: "Delay (s)",
    numbering: "Numbering", numbering_category: "Person + category (UserID = person × 10 + category)",
    numbering_plain: "One UserID per person (no categories)",
    backups: "Backups",
    backups_help: "People, fingerprints, cards, access rights and time zones. Contains biometric data: stored in the Home Assistant configuration folder and included in its backups.",
    on_change: "On every change", nightly: "Every night", keep: "Keep", backup_now: "Back up now",
    download: "Download", restore: "Restore…", import_file: "Import a file…",
    restore_title: "Restore {name}", merge: "Merge (add and update)", replace_all: "Replace (exact copy)",
    restore_help: "A backup of the current state is made first.",
    general: "General", notify_keypad: "Notify when a user is added on the keypad",
    saved: "Saved", error: "Error", loading: "Loading…", no_device: "No ZKAccess device is configured.",
    reason_change: "Change detected", reason_nightly: "Nightly", reason_manual: "Manual",
  },
  fr: {
    title: "Contrôle d'accès", device: "Appareil", connected: "Connecté", disconnected: "Déconnecté",
    tab_people: "Personnes", tab_activity: "Journal", tab_door: "Porte", tab_settings: "Réglages",
    people: "personnes", fingerprints: "empreintes", cards: "cartes", search: "Rechercher un nom ou un numéro",
    learn_card: "Apprendre une carte", new_person: "+ Nouvelle personne", person: "Personne", person_no: "Personne n° {n}",
    categories_col: "Catégories (empreintes)", card: "Carte", code: "Code", last_access: "Dernier passage",
    access: "Accès", active: "Actif", disabled: "Désactivé", yes: "Oui", none: "—", never: "Jamais",
    unassigned: "Non rattachés",
    unassigned_help: "UserID présents dans le lecteur qui ne suivent pas la numérotation « personne + catégorie » (ex. créés au clavier).",
    assign: "Rattacher à une personne…", delete: "Supprimer", cancel: "Annuler", save: "Enregistrer", done: "Terminer", close: "Fermer",
    back: "← Personnes", access_enabled: "Accès actif", fingerprints_title: "Empreintes",
    fingers_help: "Chaque doigt enregistré appartient à une catégorie. Cliquez sur un doigt libre pour l'enregistrer, ou sur un doigt enregistré pour changer sa catégorie ou le supprimer.",
    left_hand: "Main gauche", right_hand: "Main droite",
    finger_little: "Auriculaire", finger_ring: "Annulaire", finger_middle: "Majeur", finger_index: "Index", finger_thumb: "Pouce",
    left: "gauche", right: "droit", category: "Catégorie", delete_fingerprint: "Supprimer l'empreinte",
    unplaced: "Empreintes non placées sur la main",
    unplaced_help: "Enregistrées au clavier : indiquez de quel doigt il s'agit pour l'afficher sur la main.",
    place_on: "Doigt", slot: "UserID {u} · emplacement {s}",
    categories_of: "Catégories", user_id: "UserID", relay: "Relais du lecteur", relay_yes: "Oui", relay_no: "Non (Home Assistant seulement)",
    creds_title: "Cartes et codes clavier",
    creds_help: "Une carte et un code par catégorie. Au clavier, le code se tape après le UserID : le même code peut servir dans toutes les catégories.",
    replace: "Remplacer", remove: "Retirer", set_code: "Définir un code", change: "Changer", code_set: "Défini (••••)",
    identity: "Identité", name: "Nom (enregistré dans le lecteur)", person_number: "Numéro de personne",
    ha_person: "Personne Home Assistant", no_ha_person: "— aucune —",
    recent: "Derniers passages", delete_person: "Supprimer la personne",
    delete_person_help: "Supprime aussi les UserID {ids}, leurs empreintes, cartes, codes et droits. Une sauvegarde est faite avant.",
    delete_person_btn: "Supprimer {name}…",
    confirm_delete_person: "Supprimer {name} et tous ses identifiants ?",
    add_fingerprint: "Ajouter une empreinte", category_of_finger: "Catégorie de ce doigt",
    no_relay_note: "« {c} » n'active pas le relais du lecteur : seul Home Assistant réagit.",
    on_keypad: "Sur le clavier du lecteur, tapez :", admin_code: "code admin", add: "ajouter",
    place_finger: "posez le doigt <b>3 fois</b>", eq_person_cat: "= personne <b>{p}</b> + catégorie <b>{d} ({c})</b>",
    start: "Commencer", waiting: "En attente de la nouvelle empreinte sur le lecteur…", enrolled: "Empreinte enregistrée.",
    timeout: "Aucune nouvelle empreinte après 5 minutes. Réessayez.",
    card_wait: "Passez la carte devant le lecteur…", card_detected: "Carte détectée", unknown_to_device: "Inconnue du lecteur",
    enter_card: "…ou tapez le numéro de la carte", write_device: "Écrire dans le lecteur",
    code_title: "Code clavier — {c}", code_label: "Code (1 à 6 chiffres)",
    new_person_title: "Nouvelle personne", number_auto: "Numéro (vide = premier libre)",
    assign_title: "Rattacher le UserID {u}", existing_person: "Personne existante", new_number: "Nouveau numéro de personne",
    filter_all: "Tout", filter_open: "Ouvertures", filter_actions: "Actions", filter_denied: "Refus",
    everyone: "Tout le monde", live: "En direct", assign_card: "Attribuer la carte…",
    res_granted: "Ouvert", res_action: "Action", res_denied: "Refusé", res_other: "Lecteur",
    method_fingerprint: "empreinte", method_card: "carte", method_code: "code clavier", method_remote: "à distance", method_other: "autre",
    second_presentation: "second passage", unknown_card: "Carte inconnue", unknown_user: "UserID {u}",
    door_n: "Porte {n} — relais de la gâche", door_sensor: "Capteur de porte", relay_state: "Relais", alarm: "Alarme",
    not_wired: "Non câblé", open_state: "Ouverte", closed_state: "Fermée", alarm_none: "Aucune", alarm_on: "Alarme",
    open_for: "Durée d'ouverture", open_door: "Ouvrir la porte", hold_open: "Maintenir ouverte", close_door: "Refermer",
    remote_note: "Les ouvertures à distance apparaissent au journal. Les automations peuvent utiliser le bouton « Ouvrir la porte » et l'action zkaccess_native.open_door.",
    door_settings: "Réglages de la porte", written_device: "(écrits dans le lecteur)",
    relay_seconds: "Durée d'activation du relais (s)", sensor_type: "Capteur de porte", sensor_0: "Aucun", sensor_1: "Normalement ouvert", sensor_2: "Normalement fermé",
    alarm_delay: "Alarme si porte restée ouverte (s)", verify_mode: "Mode de vérification (valeur du lecteur)",
    free_passage: "Plage d'ouverture libre (0 = aucune)", close_and_lock: "Reverrouiller dès la fermeture",
    save_device: "Enregistrer dans le lecteur", clock: "Horloge du lecteur", sync_now: "Remettre à l'heure", device_info: "Appareil",
    model: "Modèle", firmware: "Firmware", doors: "Portes", cancel_alarm: "Annuler l'alarme",
    cancel_alarm_help: "Arrête l'alarme du lecteur (arrachement, porte forcée…).",
    action_categories: "Catégories d'action",
    categories_help: "UserID = numéro de personne (2 chiffres) + chiffre de catégorie. Ex. personne 12 + catégorie 3 = UserID 123. Les automations réagissent à « category » dans l'événement d'accès.",
    second_help: "Second passage : l'action de la catégorie part tout de suite ; présenter à nouveau la même carte ou le même doigt dans le délai lance aussi la seconde catégorie (gesture : double).",
    digit: "Chiffre", type: "Type", display_name: "Nom affiché", second_pres: "Second passage", used_by: "Utilisée par",
    unused: "— non utilisé —", type_open: "Ouvrir", type_close: "Fermer", type_lock: "Verrouiller", type_unlock: "Déverrouiller",
    type_arm: "Armer", type_disarm: "Désarmer", type_custom: "Personnalisé", fixed: "(fixe)", within: "Délai (s)",
    numbering: "Numérotation", numbering_category: "Personne + catégorie (UserID = personne × 10 + catégorie)",
    numbering_plain: "Un UserID par personne (sans catégories)",
    backups: "Sauvegardes",
    backups_help: "Personnes, empreintes, cartes, droits et plages horaires. Contient des données biométriques : stockées dans le dossier de configuration de Home Assistant et incluses dans ses sauvegardes.",
    on_change: "À chaque changement", nightly: "Chaque nuit", keep: "Conserver", backup_now: "Sauvegarder maintenant",
    download: "Télécharger", restore: "Restaurer…", import_file: "Importer un fichier…",
    restore_title: "Restaurer {name}", merge: "Fusionner (ajoute et met à jour)", replace_all: "Remplacer (copie exacte)",
    restore_help: "Une sauvegarde de l'état actuel est faite avant.",
    general: "Général", notify_keypad: "Notifier quand un utilisateur est ajouté au clavier",
    saved: "Enregistré", error: "Erreur", loading: "Chargement…", no_device: "Aucun appareil ZKAccess n'est configuré.",
    reason_change: "Changement détecté", reason_nightly: "Nocturne", reason_manual: "Manuelle",
  },
};

const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const STYLE = `
:host { display: block; min-height: 100vh; background: var(--primary-background-color, #f4f6f8);
  color: var(--primary-text-color, #1f2933); font-family: var(--paper-font-body1_-_font-family, Roboto, system-ui, sans-serif);
  --zk-accent: #0f766e; --zk-muted: var(--secondary-text-color, #52606d); --zk-line: var(--divider-color, #e3e8ee);
  --zk-card: var(--card-background-color, #fff); }
* { box-sizing: border-box; }
header { background: var(--app-header-background-color, var(--zk-card)); color: var(--app-header-text-color, inherit);
  border-bottom: 1px solid var(--zk-line); position: sticky; top: 0; z-index: 2; }
.bar { max-width: 1200px; margin: 0 auto; padding: 10px 16px 0; display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.bar h1 { margin: 0; font-size: 20px; font-weight: 500; }
nav { max-width: 1200px; margin: 0 auto; padding: 0 16px; display: flex; flex-wrap: wrap; gap: 24px; }
.tab { font: inherit; font-weight: 500; background: none; border: none; border-bottom: 3px solid transparent;
  padding: 12px 2px; color: inherit; opacity: .75; min-height: 44px; cursor: pointer; }
.tab.on { opacity: 1; border-bottom-color: var(--zk-accent); color: var(--zk-accent); }
main { max-width: 1200px; margin: 0 auto; padding: 20px 16px 48px; display: flex; flex-direction: column; gap: 18px; }
.card { background: var(--zk-card); border: 1px solid var(--zk-line); border-radius: 12px; padding: 18px; }
.card h2 { margin: 0 0 10px; font-size: 16px; font-weight: 500; }
.sub, .muted { font-size: 13px; color: var(--zk-muted); }
.sub { margin: 0 0 14px; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.cols { display: flex; flex-wrap: wrap; gap: 18px; align-items: flex-start; }
.grow { flex: 999 1 560px; min-width: 0; display: flex; flex-direction: column; gap: 18px; }
.side { flex: 1 1 320px; min-width: 0; display: flex; flex-direction: column; gap: 18px; }
.btn { font: inherit; font-weight: 500; border-radius: 8px; padding: 8px 14px; min-height: 40px;
  border: 1px solid var(--zk-line); background: var(--zk-card); color: inherit; cursor: pointer; }
.btn.primary { background: var(--zk-accent); border-color: var(--zk-accent); color: #fff; }
.btn.danger { color: var(--error-color, #b42318); border-color: color-mix(in srgb, var(--error-color, #b42318) 40%, transparent); }
.btn:disabled { opacity: .5; cursor: default; }
.pill { display: inline-flex; align-items: center; gap: 6px; border-radius: 999px; padding: 3px 10px; font-size: 12px; font-weight: 500; white-space: nowrap; }
.status { margin-left: auto; }
input, select { font: inherit; font-size: 14px; color: inherit; background: var(--zk-card); padding: 8px 10px;
  border-radius: 8px; border: 1px solid var(--zk-line); min-height: 40px; }
label.field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; color: var(--zk-muted); }
label.field input, label.field select { color: var(--primary-text-color, #1f2933); }
label.chk { display: inline-flex; align-items: center; gap: 8px; min-height: 40px; font-size: 14px; }
input[type=checkbox], input[type=radio] { width: 18px; height: 18px; min-height: 0; accent-color: var(--zk-accent); }
.tablewrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
th { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--zk-muted); font-weight: 500;
  text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--zk-line); }
td { padding: 10px 12px; border-bottom: 1px solid var(--zk-line); font-size: 14px; vertical-align: middle; }
tr.click { cursor: pointer; } tr.click:hover td { background: color-mix(in srgb, var(--zk-accent) 6%, transparent); }
.avatar { width: 36px; height: 36px; border-radius: 50%; color: #fff; display: inline-flex; align-items: center;
  justify-content: center; font-weight: 500; flex: none; }
.mono { font-family: ui-monospace, monospace; }
.warn { background: color-mix(in srgb, #f59e0b 12%, var(--zk-card)); border-color: color-mix(in srgb, #f59e0b 45%, transparent); }
.hands { display: flex; flex-wrap: wrap; justify-content: center; gap: 44px; }
.hand { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.fingers { display: flex; align-items: flex-end; gap: 12px; }
.fcol { display: flex; flex-direction: column; align-items: center; gap: 5px; width: 64px; }
.finger { font: inherit; width: 52px; border-radius: 26px 26px 12px 12px; cursor: pointer; display: flex;
  align-items: flex-start; justify-content: center; padding-top: 10px; color: #fff; }
.finger.free { background: transparent; border: 2px dashed var(--zk-muted); color: var(--zk-muted); font-size: 20px; }
.finger.sel { outline: 3px solid #f59e0b; outline-offset: 3px; }
.fname { font-size: 12px; color: var(--zk-muted); text-align: center; }
.detail { margin-top: 18px; border: 1px solid var(--zk-line); border-radius: 10px; padding: 14px; display: flex; flex-wrap: wrap; align-items: flex-end; gap: 14px; }
.list > div { padding: 10px 0; border-top: 1px solid var(--zk-line); }
.ev { display: flex; align-items: center; gap: 14px; padding: 12px 16px; border-bottom: 1px solid var(--zk-line); }
.ev .dot { width: 34px; height: 34px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-weight: 700; flex: none; }
.chip { font: inherit; font-size: 14px; border-radius: 999px; padding: 6px 14px; min-height: 36px; border: 1px solid var(--zk-line); background: var(--zk-card); color: inherit; cursor: pointer; }
.chip.on { background: var(--zk-accent); border-color: var(--zk-accent); color: #fff; }
.key { display: inline-flex; align-items: center; justify-content: center; min-width: 32px; height: 32px; padding: 0 8px;
  border-radius: 8px; border: 1px solid var(--zk-line); border-bottom-width: 3px; font-weight: 700; }
.key.big { min-width: 40px; height: 40px; font-size: 18px; color: var(--zk-accent); border-color: var(--zk-accent); }
.stat { display: flex; flex-direction: column; gap: 4px; padding: 12px; border-radius: 10px; background: color-mix(in srgb, var(--zk-line) 40%, transparent); flex: 1 1 130px; }
.stat b { font-size: 17px; font-weight: 500; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px; }
.modal { position: fixed; inset: 0; background: rgba(15, 23, 42, .55); display: flex; align-items: flex-start; justify-content: center;
  padding: 48px 16px; z-index: 10; overflow-y: auto; }
.dlg { background: var(--zk-card); border-radius: 16px; padding: 22px; width: 100%; max-width: 560px; display: flex; flex-direction: column; gap: 16px;
  box-shadow: 0 20px 50px rgba(15, 23, 42, .3); }
.dlg h2 { margin: 0; font-size: 19px; font-weight: 500; }
.actions { display: flex; justify-content: flex-end; gap: 10px; flex-wrap: wrap; }
.toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #1f2933; color: #fff; padding: 10px 18px;
  border-radius: 8px; z-index: 20; font-size: 14px; max-width: 90vw; }
.toast.err { background: #b42318; }
.spin { width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--zk-accent); border-right-color: transparent;
  display: inline-block; animation: s 1s linear infinite; }
@keyframes s { to { transform: rotate(360deg); } }
@media (max-width: 600px) { .bar h1 { font-size: 18px; } .status { margin-left: 0; } }
`;

class ZkAccessPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._s = { view: "people", devices: [], entryId: null, state: null, history: [], filter: "all",
      personFilter: "", search: "", person: null, finger: null, modal: null, door: null, backups: [], loading: true };
    this._unsub = null;
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first) this._start();
    if (this._menu) this._menu.hass = hass;
  }

  set narrow(narrow) {
    this._narrow = narrow;
    if (this._menu) this._menu.narrow = narrow;
  }

  disconnectedCallback() {
    if (this._unsub) { this._unsub.then((u) => u()).catch(() => {}); this._unsub = null; }
  }

  connectedCallback() {
    if (this._hass && !this._unsub && this._s.entryId) this._subscribe();
  }

  // ------------------------------------------------------------------ helpers
  t(key, vars) {
    const lang = (this._hass?.locale?.language || this._hass?.language || "en").split("-")[0];
    let s = (I18N[lang] && I18N[lang][key]) || I18N.en[key] || key;
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
    return s;
  }

  ws(type, data = {}) {
    return this._hass.connection.sendMessagePromise({ type: `${DOMAIN}/${type}`, entry_id: this._s.entryId, ...data });
  }

  async call(type, data, okMessage) {
    try {
      const r = await this.ws(type, data);
      if (okMessage) this.toast(okMessage);
      return r;
    } catch (e) {
      this.toast(`${this.t("error")}: ${e.message || e.code || e}`, true);
      throw e;
    }
  }

  toast(msg, err = false) {
    this._toast = { msg, err };
    this.render();
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { this._toast = null; this.render(); }, 3500);
  }

  fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString(this._hass.locale?.language || undefined,
      { weekday: "short", hour: "2-digit", minute: "2-digit", day: "2-digit", month: "2-digit" });
  }

  cats() { return this._s.state?.categories || {}; }
  catName(d) { const c = this.cats()[String(d)]; return c ? c.name : `#${d}`; }
  color(d) { return PALETTE[Number(d) % PALETTE.length]; }
  plain() { return this._s.state?.numbering === "plain"; }
  persons() { return this._s.state?.snapshot?.persons || []; }
  findPerson(num) { return this.persons().find((p) => p.num === num); }
  fingerName(i) { return `${this.t("finger_" + FINGER_KEYS[i])} ${i < 5 ? this.t("left") : this.t("right")}`; }

  // ------------------------------------------------------------------ loading
  async _start() {
    try {
      this._s.devices = await this._hass.connection.sendMessagePromise({ type: `${DOMAIN}/devices` });
    } catch (e) { this._s.devices = []; }
    this._s.entryId = this._s.devices[0]?.entry_id || null;
    if (this._s.entryId) await this._loadDevice(); else { this._s.loading = false; this.render(); }
  }

  async _loadDevice() {
    this._s.loading = true; this.render();
    if (this._unsub) { this._unsub.then((u) => u()).catch(() => {}); this._unsub = null; }
    try {
      this._s.state = await this.ws("state");
      this._s.history = await this.ws("history", { limit: 300 });
    } catch (e) { this.toast(`${this.t("error")}: ${e.message || e}`, true); }
    this._s.loading = false;
    this._subscribe();
    this.render();
  }

  _subscribe() {
    this._unsub = this._hass.connection.subscribeMessage((msg) => this._onPush(msg),
      { type: `${DOMAIN}/subscribe`, entry_id: this._s.entryId });
  }

  async _onPush(msg) {
    if (msg.kind === "event") {
      this._s.history.unshift(msg.event);
      this._s.history.length = Math.min(this._s.history.length, 500);
      const m = this._s.modal;
      if (m && m.kind === "card" && !m.card && msg.event.card && !msg.event.person) {
        m.card = msg.event.card; m.cardInput = String(msg.event.card);
      }
      this.render();
    } else if (msg.kind === "changed") {
      if (msg.enroll && this._s.modal?.kind === "enroll") {
        this._s.modal.step = msg.enroll.state === "done" ? "done" : "timeout";
      }
      clearTimeout(this._reloadTimer);
      this._reloadTimer = setTimeout(async () => {
        try { this._s.state = await this.ws("state"); this.render(); } catch (e) { /* offline */ }
      }, 300);
    }
  }

  async refresh() {
    this._s.state = await this.ws("state", { refresh: true });
    this.render(false);
  }

  // ------------------------------------------------------------------ actions
  async act(a, el) {
    const s = this._s, d = el.dataset;
    switch (a) {
      case "tab": s.view = d.v; s.person = null; s.finger = null;
        if (d.v === "door") this.loadDoor(); if (d.v === "settings") this.loadBackups(); break;
      case "device": s.entryId = el.value; s.person = null; await this._loadDevice(); return;
      case "open-person": s.person = Number(d.p); s.finger = null; s.view = "people"; break;
      case "back": s.person = null; s.finger = null; break;
      case "new-person": s.modal = { kind: "person", name: "", num: "" }; break;
      case "save-new-person": {
        const name = this.val("np-name"), num = this.val("np-num");
        const p = await this.call("person/save", { name, person: num ? Number(num) : null });
        s.modal = null; await this.refresh(); s.person = p; break;
      }
      case "toggle-person": await this.call("person/enable", { person: Number(d.p), enabled: el.checked }); await this.refresh(); return;
      case "save-identity": {
        await this.call("person/save", { person: s.person, name: this.val("id-name"), ha_person: this.val("id-ha") || null }, this.t("saved"));
        await this.refresh(); return;
      }
      case "delete-person": s.modal = { kind: "confirm", text: this.t("confirm_delete_person", { name: esc(this.findPerson(s.person)?.name || s.person) }),
        then: async () => { await this.call("person/delete", { person: s.person }); s.person = null; await this.refresh(); } }; break;
      case "finger": {
        const i = Number(d.i), f = this.fingerAt(i);
        if (f) s.finger = { user_id: f.user_id, finger_id: f.finger_id, physical: i };
        else { s.finger = null; this.openEnroll(i); }
        break;
      }
      case "finger-cat": {
        const f = s.finger; const r = await this.call("finger/update", { user_id: f.user_id, finger_id: f.finger_id, digit: Number(el.value) });
        s.finger = { ...f, user_id: r[0], finger_id: r[1] }; await this.refresh(); return;
      }
      case "finger-place": {
        await this.call("finger/update", { user_id: Number(d.u), finger_id: Number(d.f), physical: el.value === "" ? null : Number(el.value) });
        await this.refresh(); return;
      }
      case "finger-move": {
        await this.call("finger/update", { user_id: Number(d.u), finger_id: Number(d.f), digit: Number(el.value) });
        await this.refresh(); return;
      }
      case "finger-delete": {
        const u = Number(d.u ?? s.finger.user_id), f = Number(d.f ?? s.finger.finger_id);
        s.modal = { kind: "confirm", text: this.t("delete_fingerprint") + " ?", then: async () => {
          await this.call("finger/delete", { user_id: u, finger_id: f }); s.finger = null; await this.refresh(); } };
        break;
      }
      case "enroll-start": {
        const m = s.modal; m.digit = Number(this.shadowRoot.querySelector("input[name=cat]:checked")?.value ?? 0);
        const r = await this.call("enroll/start", { person: m.person, digit: m.digit, physical: m.physical });
        m.user_id = r.user_id; m.step = "wait"; break;
      }
      case "enroll-cancel": if (s.modal?.step === "wait") await this.ws("enroll/cancel").catch(() => {}); s.modal = null; await this.refresh(); return;
      case "learn-card": s.modal = { kind: "card", person: d.p ? Number(d.p) : (s.person ?? this.persons()[0]?.num ?? null),
        digit: Number(d.d ?? 0), card: d.card ? Number(d.card) : null, cardInput: d.card || "" }; break;
      case "card-save": {
        const m = s.modal, card = Number(this.val("card-no"));
        await this.call("credential/set", { person: Number(this.val("card-person")), digit: Number(this.val("card-digit")), card }, this.t("saved"));
        s.modal = null; await this.refresh(); break;
      }
      case "card-remove": await this.call("credential/set", { person: Number(d.p), digit: Number(d.d), card: null }); await this.refresh(); return;
      case "code-edit": s.modal = { kind: "code", person: Number(d.p), digit: Number(d.d) }; break;
      case "code-save": {
        const m = s.modal; await this.call("credential/set", { person: m.person, digit: m.digit, code: this.val("code-val") }, this.t("saved"));
        s.modal = null; await this.refresh(); break;
      }
      case "code-remove": await this.call("credential/set", { person: Number(d.p), digit: Number(d.d), code: null }); await this.refresh(); return;
      case "assign": s.modal = { kind: "assign", user_id: Number(d.u) }; break;
      case "assign-save": {
        const m = s.modal; const existing = this.val("as-person"); const num = existing || this.val("as-new");
        await this.call("unassigned/assign", { user_id: m.user_id, person: Number(num), digit: Number(this.val("as-digit") || 0) });
        s.modal = null; await this.refresh(); break;
      }
      case "user-delete": s.modal = { kind: "confirm", text: `${this.t("delete")} UserID ${d.u} ?`,
        then: async () => { await this.call("user/delete", { user_id: Number(d.u) }); await this.refresh(); } }; break;
      case "confirm-ok": { const m = s.modal; s.modal = null; this.render(); await m.then(); break; }
      case "close-modal": s.modal = null; break;
      case "filter": s.filter = d.f; break;
      case "door-ctl": await this.call("door/control", { door: Number(d.door || 1), action: d.a, seconds: Number(this.val("open-for") || 5) }, this.t("saved")); return;
      case "door-save": {
        const v = {}; for (const k of ["relay_seconds", "sensor_type", "alarm_delay", "verify_mode", "free_passage_timezone"]) {
          const x = this.val("ds-" + k); if (x !== "") v[k] = Number(x); }
        v.close_and_lock = this.shadowRoot.getElementById("ds-close_and_lock").checked ? 1 : 0;
        await this.call("door/set", { door: 1, settings: v }, this.t("saved")); await this.loadDoor(); return;
      }
      case "clock-sync": await this.call("clock/sync", {}, this.t("saved")); await this.loadDoor(); return;
      case "cats-save": await this.saveCategories(); return;
      case "settings-save": {
        await this.call("settings/save", { backup: { on_change: this.chk("bk-change"), nightly: this.chk("bk-nightly"), keep: Number(this.val("bk-keep")) },
          notify_keypad: this.chk("notify-keypad") }, this.t("saved"));
        await this.refresh(); return;
      }
      case "backup-now": await this.call("backup/create", {}, this.t("saved")); await this.loadBackups(); return;
      case "backup-download": {
        const content = await this.call("backup/download", { name: d.n });
        const url = URL.createObjectURL(new Blob([content], { type: "application/json" }));
        const a = document.createElement("a"); a.href = url; a.download = d.n; a.click(); setTimeout(() => URL.revokeObjectURL(url), 2000); return;
      }
      case "backup-restore": s.modal = { kind: "restore", name: d.n }; break;
      case "restore-ok": {
        const m = s.modal; const mode = this.shadowRoot.querySelector("input[name=mode]:checked").value;
        s.modal = null; this.render();
        await this.call("backup/restore", { name: m.name, mode }, this.t("saved")); await this.refresh(); await this.loadBackups(); return;
      }
      case "backup-import": this.shadowRoot.getElementById("import-file").click(); return;
      default: return;
    }
    this.render();
  }

  val(id) { const e = this.shadowRoot.getElementById(id); return e ? e.value.trim() : ""; }
  chk(id) { const e = this.shadowRoot.getElementById(id); return !!(e && e.checked); }

  fingersOf(person) {
    const out = [];
    for (const c of person?.categories || []) for (const f of c.fingers) out.push({ ...f, user_id: c.user_id, digit: c.digit });
    return out;
  }
  fingerAt(i) { return this.fingersOf(this.findPerson(this._s.person)).find((f) => f.physical === i); }

  openEnroll(physical) {
    this._s.modal = { kind: "enroll", person: this._s.person, physical, step: "choose" };
  }

  async loadDoor() {
    try { this._s.door = await this.ws("door/get", { door: 1 }); } catch (e) { this._s.door = null; }
    this.render();
  }

  async loadBackups() {
    try { this._s.backups = await this.ws("backup/list"); } catch (e) { this._s.backups = []; }
    this.render();
  }

  async saveCategories() {
    const out = {};
    for (let d = 0; d <= 9; d++) {
      const type = d === 0 ? "open" : this.val(`ct-type-${d}`);
      if (!type) continue;
      const second = this.val(`ct-second-${d}`);
      out[String(d)] = { type, name: this.val(`ct-name-${d}`) || this.t("type_" + type), relay: this.chk(`ct-relay-${d}`),
        second: second === "" ? null : Number(second) };
    }
    const numbering = this.val("numbering") || "category";
    await this.call("categories/save", { categories: out, second_window: Number(this.val("second-window") || 3), numbering }, this.t("saved"));
    await this.refresh();
  }

  async importFile(file) {
    const content = await file.text();
    await this.call("backup/import", { content }, this.t("saved"));
    await this.loadBackups();
  }

  // ------------------------------------------------------------------- render
  // Values typed in inputs survive re-renders (live events) while the screen stays the same.
  context() {
    const s = this._s;
    return `${s.entryId}|${s.view}|${s.person}|${s.finger ? s.finger.user_id + ":" + s.finger.finger_id : ""}|${s.modal ? s.modal.kind + ":" + (s.modal.step || "") : ""}`;
  }

  captureInputs() {
    const out = {};
    this.shadowRoot.querySelectorAll("input[id], select[id]").forEach((e) => {
      if (e.type !== "file") out[e.id] = e.type === "checkbox" ? e.checked : e.value;
    });
    const radios = {};
    this.shadowRoot.querySelectorAll("input[type=radio]:checked").forEach((e) => { radios[e.name] = e.value; });
    const active = this.shadowRoot.activeElement;
    return { values: out, radios, focus: active && active.id ? active.id : null };
  }

  restoreInputs(saved) {
    for (const [id, v] of Object.entries(saved.values)) {
      const e = this.shadowRoot.getElementById(id);
      if (!e || e.dataset.act) continue;
      if (e.type === "checkbox") e.checked = v; else e.value = v;
    }
    for (const [name, v] of Object.entries(saved.radios)) {
      const e = this.shadowRoot.querySelector(`input[type=radio][name="${name}"][value="${v}"]`);
      if (e) e.checked = true;
    }
    if (saved.focus) {
      const e = this.shadowRoot.getElementById(saved.focus);
      if (e) { e.focus(); if (e.setSelectionRange && e.type !== "number") try { e.setSelectionRange(e.value.length, e.value.length); } catch (x) { /* */ } }
    }
  }

  render(keepInputs = true) {
    const s = this._s, root = this.shadowRoot;
    const scroll = window.scrollY;
    const ctx = this.context();
    const saved = keepInputs && this._ctx === ctx ? this.captureInputs() : null;
    this._ctx = ctx;
    let body;
    if (s.loading) body = `<main><p class="muted">${this.t("loading")}</p></main>`;
    else if (!s.entryId) body = `<main><div class="card">${this.t("no_device")}</div></main>`;
    else if (s.view === "people") body = s.person != null && this.findPerson(s.person) ? this.renderPerson() : this.renderPeople();
    else if (s.view === "activity") body = this.renderActivity();
    else if (s.view === "door") body = this.renderDoor();
    else body = this.renderSettings();
    root.innerHTML = `<style>${STYLE}</style>${this.renderHeader()}${body}${this.renderModal()}
      ${this._toast ? `<div class="toast ${this._toast.err ? "err" : ""}">${esc(this._toast.msg)}</div>` : ""}
      <input type="file" id="import-file" accept="application/json" hidden>`;
    this.bind();
    if (saved) this.restoreInputs(saved);
    window.scrollTo(0, scroll);
  }

  bind() {
    const root = this.shadowRoot;
    root.querySelectorAll("[data-act]").forEach((el) => {
      const ev = el.tagName === "SELECT" || el.type === "checkbox" ? "change" : "click";
      el.addEventListener(ev, (e) => { if (ev === "click") e.preventDefault(); this.act(el.dataset.act, el).catch(() => {}); });
    });
    const search = root.getElementById("search");
    if (search) search.addEventListener("input", () => { this._s.search = search.value; this.render(); });
    const pf = root.getElementById("person-filter");
    if (pf) pf.addEventListener("change", () => { this._s.personFilter = pf.value; this.render(); });
    const file = root.getElementById("import-file");
    file.addEventListener("change", () => { if (file.files[0]) this.importFile(file.files[0]).catch(() => {}); });
    const slot = root.getElementById("menu-slot");
    if (slot) {
      if (!this._menu) this._menu = document.createElement("ha-menu-button");
      this._menu.hass = this._hass; this._menu.narrow = this._narrow;
      slot.appendChild(this._menu);
    }
    const cardIn = root.getElementById("card-no");
    if (cardIn) cardIn.addEventListener("input", () => { this._s.modal.cardInput = cardIn.value; });
  }

  renderHeader() {
    const s = this._s, st = s.state;
    const connected = st?.device?.connected;
    const devSel = s.devices.length > 1
      ? `<label class="row muted">${this.t("device")} <select data-act="device">${s.devices.map((d) =>
        `<option value="${d.entry_id}" ${d.entry_id === s.entryId ? "selected" : ""}>${esc(d.title)}</option>`).join("")}</select></label>`
      : `<span class="muted">${esc(st?.device?.title || "")}</span>`;
    const tabs = ["people", "activity", "door", "settings"].map((v) =>
      `<button class="tab ${s.view === v ? "on" : ""}" data-act="tab" data-v="${v}">${this.t("tab_" + v)}</button>`).join("");
    return `<header><div class="bar"><span id="menu-slot"></span><h1>${this.t("title")}</h1>${devSel}
      ${st ? `<span class="pill status" style="background:${connected ? "#e7f6ee" : "#fdecea"};color:${connected ? "#067647" : "#b42318"}">● ${this.t(connected ? "connected" : "disconnected")}</span>` : ""}
      </div><nav>${tabs}</nav></header>`;
  }

  catPill(digit, label) {
    const c = this.color(digit);
    return `<span class="pill" style="background:${c}1f;color:${c}">${esc(label)}</span>`;
  }

  lastOf(num) {
    const e = this._s.history.find((h) => h.person === num && (h.result === "granted" || h.result === "action"));
    return e ? `${this.fmtTime(e.time)} · ${esc(e.category || this.t("method_" + e.method))}` : this.t("never");
  }

  renderPeople() {
    const snap = this._s.state?.snapshot || { persons: [], unassigned: [], counts: {} };
    const q = this._s.search.toLowerCase();
    const list = snap.persons.filter((p) => !q || (p.name || "").toLowerCase().includes(q) || String(p.num).includes(q));
    const rows = list.map((p) => {
      const cats = p.categories.map((c) => this.catPill(c.digit, `${this.catName(c.digit)} · ${c.fingers.length}`)).join(" ");
      const card = p.categories.some((c) => c.card), code = p.categories.some((c) => c.has_code);
      return `<tr class="click" data-act="open-person" data-p="${p.num}">
        <td><div class="row"><span class="avatar" style="background:${this.color(p.num)}">${esc((p.name || "?")[0].toUpperCase())}</span>
          <div><b style="font-weight:500">${esc(p.name || this.t("person_no", { n: p.num }))}</b><div class="muted">${this.t("person_no", { n: p.num })}</div></div></div></td>
        ${this.plain() ? `<td>${p.categories[0]?.fingers.length || 0}</td>` : `<td><div class="row" style="gap:6px">${cats}</div></td>`}
        <td>${card ? this.t("yes") : this.t("none")}</td><td>${code ? this.t("yes") : this.t("none")}</td>
        <td class="muted">${this.lastOf(p.num)}</td>
        <td><span class="pill" style="background:${p.enabled ? "#e7f6ee" : "#f1f4f7"};color:${p.enabled ? "#067647" : "#52606d"}">${this.t(p.enabled ? "active" : "disabled")}</span></td></tr>`;
    }).join("");
    const unassigned = snap.unassigned.length ? `<section class="card warn"><div class="row"><h2 style="margin:0">${this.t("unassigned")}</h2>
      <span class="muted">${this.t("unassigned_help")}</span></div><div class="list">${snap.unassigned.map((u) => `
      <div class="row"><span class="mono" style="font-weight:700">UserID ${u.user_id}</span>
        <span class="muted" style="flex:1 1 220px">${esc(u.name)} · ${u.fingers.length} ${this.t("fingerprints")}${u.card ? ` · ${this.t("card")} #${u.card}` : ""}</span>
        <button class="btn danger" data-act="user-delete" data-u="${u.user_id}">${this.t("delete")}</button>
        <button class="btn primary" data-act="assign" data-u="${u.user_id}">${this.t("assign")}</button></div>`).join("")}</div></section>` : "";
    return `<main>
      <div class="row"><div class="row muted" style="gap:20px">
        <span><b style="font-size:20px;color:var(--primary-text-color)">${snap.counts.persons ?? 0}</b> ${this.t("people")}</span>
        <span><b style="font-size:20px;color:var(--primary-text-color)">${snap.counts.fingerprints ?? 0}</b> ${this.t("fingerprints")}</span>
        <span><b style="font-size:20px;color:var(--primary-text-color)">${snap.counts.cards ?? 0}</b> ${this.t("cards")}</span></div>
        <input id="search" type="search" placeholder="${this.t("search")}" value="${esc(this._s.search)}" style="margin-left:auto;width:260px" aria-label="${this.t("search")}">
        <button class="btn" data-act="learn-card">${this.t("learn_card")}</button>
        <button class="btn primary" data-act="new-person">${this.t("new_person")}</button></div>
      <div class="card tablewrap" style="padding:0"><table style="min-width:820px"><thead><tr><th>${this.t("person")}</th>
        <th>${this.plain() ? this.t("fingerprints_title") : this.t("categories_col")}</th><th>${this.t("card")}</th><th>${this.t("code")}</th>
        <th>${this.t("last_access")}</th><th>${this.t("access")}</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${unassigned}</main>`;
  }

  renderHand(from, to, label) {
    const cols = [];
    for (let i = from; i <= to; i++) cols.push(i);
    const s = this._s;
    const html = cols.map((i) => {
      const f = this.fingerAt(i), key = FINGER_KEYS[i];
      const sel = s.finger && s.finger.physical === i;
      const style = `height:${FINGER_HEIGHT[key]}px;${f ? `background:${this.color(f.digit)}` : ""}`;
      return `<div class="fcol"><button class="finger ${f ? "" : "free"} ${sel ? "sel" : ""}" data-act="finger" data-i="${i}" style="${style}"
        aria-label="${esc(this.fingerName(i))}">${f ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 11v4"/><path d="M8 15v-3a4 4 0 0 1 8 0v4"/><path d="M5 14v-2a7 7 0 0 1 14 0v5"/></svg>` : "+"}</button>
        <span class="fname">${this.t("finger_" + key)}</span>${f && !this.plain() ? this.catPill(f.digit, this.catName(f.digit)) : ""}</div>`;
    }).join("");
    return `<div class="hand"><div class="fingers">${html}</div><b class="muted">${label}</b></div>`;
  }

  renderPerson() {
    const s = this._s, p = this.findPerson(s.person);
    const cats = this.cats();
    const ids = p.categories.map((c) => c.user_id).join(", ");
    let detail = "";
    if (s.finger) {
      const f = this.fingersOf(p).find((x) => x.user_id === s.finger.user_id && x.finger_id === s.finger.finger_id);
      if (f) detail = `<div class="detail"><div style="flex:1 1 200px"><b style="font-weight:500">${esc(this.fingerName(f.physical))}</b>
        <div class="muted">${this.t("slot", { u: f.user_id, s: f.finger_id })}</div></div>
        ${this.plain() ? "" : `<label class="field">${this.t("category")}<select data-act="finger-cat">${Object.keys(cats).map((d) =>
          `<option value="${d}" ${Number(d) === f.digit ? "selected" : ""}>${esc(cats[d].name)} (${p.num * 10 + Number(d)})</option>`).join("")}</select></label>`}
        <button class="btn danger" data-act="finger-delete">${this.t("delete_fingerprint")}</button></div>`;
    }
    const unplaced = this.fingersOf(p).filter((f) => f.physical == null);
    const unplacedHtml = unplaced.length ? `<div class="detail" style="flex-direction:column;align-items:stretch"><b style="font-weight:500">${this.t("unplaced")}</b>
      <span class="muted">${this.t("unplaced_help")}</span>${unplaced.map((f) => `<div class="row">
        <span class="mono" style="flex:1 1 160px">${this.t("slot", { u: f.user_id, s: f.finger_id })}</span>
        ${this.plain() ? "" : this.catPill(f.digit, this.catName(f.digit))}
        <label class="row muted">${this.t("place_on")}<select data-act="finger-place" data-u="${f.user_id}" data-f="${f.finger_id}"><option value="">—</option>
          ${FINGER_KEYS.map((_, i) => `<option value="${i}">${esc(this.fingerName(i))}</option>`).join("")}</select></label>
        <button class="btn danger" data-act="finger-delete" data-u="${f.user_id}" data-f="${f.finger_id}">${this.t("delete")}</button></div>`).join("")}</div>` : "";
    const catRows = Object.keys(cats).map((d) => {
      const c = p.categories.find((x) => x.digit === Number(d));
      const uid = this.plain() ? p.num : p.num * 10 + Number(d);
      return { d: Number(d), c, uid };
    }).filter((r) => !this.plain() || r.d === 0);
    const creds = catRows.map(({ d, c, uid }) => `<div style="display:flex;flex-direction:column;gap:8px">
      ${this.plain() ? "" : `<span>${this.catPill(d, `${this.catName(d)} · ${uid}`)}</span>`}
      <div class="row"><span class="muted" style="width:44px">${this.t("card")}</span><span class="mono" style="flex:1">${c?.card ? "#" + c.card : this.t("none")}</span>
        <button class="btn" data-act="learn-card" data-p="${p.num}" data-d="${d}">${c?.card ? this.t("replace") : this.t("learn_card")}</button>
        ${c?.card ? `<button class="btn danger" data-act="card-remove" data-p="${p.num}" data-d="${d}">${this.t("remove")}</button>` : ""}</div>
      <div class="row"><span class="muted" style="width:44px">${this.t("code")}</span><span style="flex:1">${c?.has_code ? this.t("code_set") : this.t("none")}</span>
        <button class="btn" data-act="code-edit" data-p="${p.num}" data-d="${d}">${c?.has_code ? this.t("change") : this.t("set_code")}</button>
        ${c?.has_code ? `<button class="btn danger" data-act="code-remove" data-p="${p.num}" data-d="${d}">${this.t("remove")}</button>` : ""}</div></div>`).join("");
    const table = this.plain() ? "" : `<section class="card tablewrap"><h2>${this.t("categories_of")}</h2><table style="min-width:560px"><thead><tr>
      <th>${this.t("category")}</th><th>${this.t("user_id")}</th><th>${this.t("fingerprints_title")}</th><th>${this.t("card")}</th><th>${this.t("code")}</th><th>${this.t("relay")}</th></tr></thead><tbody>
      ${p.categories.map((c) => `<tr><td>${this.catPill(c.digit, this.catName(c.digit))}</td><td class="mono">${c.user_id}</td><td>${c.fingers.length}</td>
        <td>${c.card ? this.t("yes") : this.t("none")}</td><td>${c.has_code ? this.t("yes") : this.t("none")}</td>
        <td>${cats[String(c.digit)]?.relay ? this.t("relay_yes") : this.t("relay_no")}</td></tr>`).join("")}</tbody></table></section>`;
    const people = Object.keys(this._hass.states).filter((e) => e.startsWith("person.")).sort();
    const recent = this._s.history.filter((h) => h.person === p.num).slice(0, 8);
    return `<main>
      <div class="row"><a href="#" data-act="back" style="color:var(--zk-accent);text-decoration:none">${this.t("back")}</a>
        <span class="avatar" style="background:${this.color(p.num)};width:46px;height:46px;font-size:19px">${esc((p.name || "?")[0].toUpperCase())}</span>
        <div><h2 style="margin:0;font-size:22px;font-weight:500">${esc(p.name || this.t("person_no", { n: p.num }))}</h2><div class="muted">${this.t("person_no", { n: p.num })}</div></div>
        <label class="chk" style="margin-left:auto;font-weight:500"><input type="checkbox" data-act="toggle-person" data-p="${p.num}" ${p.enabled ? "checked" : ""}>${this.t("access_enabled")}</label></div>
      <div class="cols"><div class="grow">
        <section class="card"><h2>${this.t("fingerprints_title")}</h2><p class="sub">${this.t("fingers_help")}</p>
          <div class="hands">${this.renderHand(0, 4, this.t("left_hand"))}${this.renderHand(5, 9, this.t("right_hand"))}</div>${detail}${unplacedHtml}</section>
        ${table}</div>
      <div class="side">
        <section class="card"><h2>${this.t("identity")}</h2><div style="display:flex;flex-direction:column;gap:12px">
          <label class="field">${this.t("name")}<input id="id-name" maxlength="24" value="${esc(p.name)}"></label>
          <label class="field">${this.t("person_number")}<input value="${p.num}" readonly></label>
          <label class="field">${this.t("ha_person")}<select id="id-ha"><option value="">${this.t("no_ha_person")}</option>
            ${people.map((e) => `<option value="${e}" ${e === p.ha_person ? "selected" : ""}>${esc(this._hass.states[e].attributes.friendly_name || e)}</option>`).join("")}</select></label>
          <div class="actions"><button class="btn primary" data-act="save-identity">${this.t("save")}</button></div></div></section>
        <section class="card"><h2>${this.t("creds_title")}</h2><p class="sub">${this.t("creds_help")}</p><div class="list">${creds}</div></section>
      </div></div>
      <div class="cols"><section class="card grow" style="display:block"><h2>${this.t("recent")}</h2><div class="list">${recent.map((h) =>
        `<div class="row"><span class="muted" style="width:150px">${this.fmtTime(h.time)}</span><span style="flex:1">${esc(h.category || h.event_name)} · ${this.t("method_" + h.method)}</span>
          <b style="font-weight:500">${this.t("res_" + h.result)}</b></div>`).join("") || `<div class="muted">${this.t("never")}</div>`}</div></section>
        <section class="card side" style="display:block;border-color:color-mix(in srgb, #b42318 35%, transparent)"><h2 style="color:var(--error-color,#b42318)">${this.t("delete_person")}</h2>
          <p class="sub">${this.t("delete_person_help", { ids: ids })}</p>
          <button class="btn danger" data-act="delete-person">${this.t("delete_person_btn", { name: esc(p.name || p.num) })}</button></section></div>
    </main>`;
  }

  renderActivity() {
    const s = this._s;
    const f = s.filter;
    const items = s.history.filter((h) => (f === "all" || (f === "open" && h.result === "granted") || (f === "actions" && h.result === "action")
      || (f === "denied" && h.result === "denied")) && (!s.personFilter || String(h.person) === s.personFilter));
    const style = { granted: ["#e7f6ee", "#067647", "✓"], action: ["#e8eefc", "#1e40af", "★"], denied: ["#fdecea", "#b42318", "✕"], other: ["#f1f4f7", "#52606d", "↗"] };
    const rows = items.slice(0, 300).map((h) => {
      const [bg, fg, icon] = style[h.result] || style.other;
      const who = h.person_name || (h.user_id ? (h.person ? this.t("person_no", { n: h.person }) : this.t("unknown_user", { u: h.user_id }))
        : h.card ? this.t("unknown_card") : h.event_name);
      const parts = [h.category, this.t("method_" + h.method), h.card && !h.person ? `#${h.card}` : null,
        h.gesture === "double" ? this.t("second_presentation") : null, h.result === "denied" || h.result === "other" ? h.event_name : null].filter(Boolean);
      const canAssign = h.card && !h.person && h.result === "denied";
      return `<div class="ev"><span class="muted" style="width:120px;font-size:13px">${this.fmtTime(h.time)}</span>
        <span class="dot" style="background:${bg};color:${fg}">${icon}</span>
        <div style="flex:1"><div style="font-weight:500">${esc(who)}</div><div class="muted">${esc(parts.join(" · "))}</div></div>
        <span class="pill" style="background:${bg};color:${fg}">${esc(h.result === "action" ? (h.category || this.t("res_action")) : this.t("res_" + h.result))}</span>
        ${canAssign ? `<button class="btn" data-act="learn-card" data-card="${h.card}">${this.t("assign_card")}</button>` : ""}</div>`;
    }).join("");
    const chips = ["all", "open", "actions", "denied"].map((k) =>
      `<button class="chip ${f === k ? "on" : ""}" data-act="filter" data-f="${k}">${this.t("filter_" + k)}</button>`).join("");
    return `<main><div class="row">${chips}<label class="row muted" style="margin-left:auto">${this.t("person")}
      <select id="person-filter"><option value="">${this.t("everyone")}</option>${this.persons().map((p) =>
        `<option value="${p.num}" ${String(p.num) === s.personFilter ? "selected" : ""}>${esc(p.name || p.num)}</option>`).join("")}</select></label>
      <span class="pill" style="background:#e7f6ee;color:#067647">● ${this.t("live")}</span></div>
      <div class="card tablewrap" style="padding:0">${rows || `<div class="ev muted">${this.t("never")}</div>`}</div></main>`;
  }

  renderDoor() {
    const st = this._s.state, door = this._s.door, ds = door?.settings || {};
    const status = st?.door_status?.[0];
    const sensor = !status || status.open == null ? this.t("not_wired") : this.t(status.open ? "open_state" : "closed_state");
    const num = (k, label) => `<label class="field">${label}<input id="ds-${k}" type="number" min="0" max="255" value="${ds[k] ?? ""}"></label>`;
    return `<main><div class="cols"><div class="grow">
      <section class="card"><h2>${this.t("door_n", { n: 1 })}</h2>
        <div class="row" style="margin-bottom:16px"><div class="stat"><span class="muted">${this.t("door_sensor")}</span><b>${sensor}</b></div>
          <div class="stat"><span class="muted">${this.t("alarm")}</span><b>${status?.alarm ? this.t("alarm_on") : this.t("alarm_none")}</b></div></div>
        <div class="row"><label class="field">${this.t("open_for")}<select id="open-for">${[3, 5, 10, 30].map((n) => `<option value="${n}" ${n === 5 ? "selected" : ""}>${n} s</option>`).join("")}</select></label>
          <button class="btn primary" style="align-self:flex-end" data-act="door-ctl" data-a="open">${this.t("open_door")}</button>
          <button class="btn" style="align-self:flex-end" data-act="door-ctl" data-a="hold">${this.t("hold_open")}</button>
          <button class="btn" style="align-self:flex-end" data-act="door-ctl" data-a="close">${this.t("close_door")}</button></div>
        <p class="sub" style="margin:14px 0 0">${this.t("remote_note")}</p></section>
      <section class="card"><h2>${this.t("door_settings")} <span class="muted" style="font-weight:400">${this.t("written_device")}</span></h2>
        ${door ? `<div class="grid">${num("relay_seconds", this.t("relay_seconds"))}
          <label class="field">${this.t("sensor_type")}<select id="ds-sensor_type">${[0, 1, 2].map((n) => `<option value="${n}" ${ds.sensor_type === n ? "selected" : ""}>${this.t("sensor_" + n)}</option>`).join("")}</select></label>
          ${num("alarm_delay", this.t("alarm_delay"))}${num("verify_mode", this.t("verify_mode"))}${num("free_passage_timezone", this.t("free_passage"))}
          <label class="chk" style="align-self:end"><input type="checkbox" id="ds-close_and_lock" ${ds.close_and_lock ? "checked" : ""}>${this.t("close_and_lock")}</label></div>
          <div class="actions" style="margin-top:14px"><button class="btn primary" data-act="door-save">${this.t("save_device")}</button></div>`
        : `<p class="muted">${this.t("loading")}</p>`}</section></div>
      <div class="side">
        <section class="card"><h2>${this.t("clock")}</h2><div style="font-size:24px;font-weight:500">${door?.device_time ? this.fmtTime(door.device_time) : "—"}</div>
          <div class="actions" style="justify-content:flex-start;margin-top:12px"><button class="btn" data-act="clock-sync">${this.t("sync_now")}</button></div></section>
        <section class="card"><h2>${this.t("device_info")}</h2><div class="list">
          <div class="row"><span class="muted" style="width:110px">${this.t("model")}</span>${esc(st?.device?.machine_type ?? "—")}</div>
          <div class="row"><span class="muted" style="width:110px">${this.t("firmware")}</span>${esc(st?.device?.firmware ?? "—")}</div>
          <div class="row"><span class="muted" style="width:110px">${this.t("doors")}</span>${esc(st?.device?.doors ?? "—")}</div>
          <div class="row"><span class="muted" style="width:110px">Serial</span><span class="mono">${esc(st?.device?.serial ?? "—")}</span></div></div></section>
        <section class="card"><h2>${this.t("alarm")}</h2><p class="sub">${this.t("cancel_alarm_help")}</p>
          <button class="btn" data-act="door-ctl" data-a="cancel_alarm">${this.t("cancel_alarm")}</button></section>
      </div></div></main>`;
  }

  renderSettings() {
    const st = this._s.state, cats = this.cats(), bk = st?.settings?.backup || {};
    const users = {};
    for (const p of this.persons()) for (const c of p.categories) (users[c.digit] ||= []).push(p.name || p.num);
    const rows = [];
    for (let d = 0; d <= 9; d++) {
      const c = cats[String(d)];
      const typeSel = d === 0 ? `${this.t("type_open")} <span class="muted">${this.t("fixed")}</span>`
        : `<select id="ct-type-${d}" aria-label="${this.t("type")} ${d}"><option value="">${this.t("unused")}</option>${CATEGORY_TYPES.filter((x) => x !== "open").map((x) =>
          `<option value="${x}" ${c?.type === x ? "selected" : ""}>${this.t("type_" + x)}</option>`).join("")}</select>`;
      const secondSel = `<select id="ct-second-${d}" aria-label="${this.t("second_pres")} ${d}"><option value="">${this.t("none")}</option>${Object.keys(cats).filter((x) => x !== String(d)).map((x) =>
        `<option value="${x}" ${c?.second === Number(x) ? "selected" : ""}>${esc(cats[x].name)}</option>`).join("")}</select>`;
      rows.push(`<tr><td class="mono" style="font-weight:700;font-size:16px">${d}</td><td>${typeSel}</td>
        <td><input id="ct-name-${d}" value="${esc(c?.name || "")}" maxlength="32" style="width:150px" aria-label="${this.t("display_name")} ${d}"></td>
        <td><input type="checkbox" id="ct-relay-${d}" ${c?.relay ? "checked" : ""} aria-label="${this.t("relay")} ${d}"></td>
        <td>${secondSel}</td><td class="muted">${esc((users[d] || []).slice(0, 4).join(", "))}</td></tr>`);
    }
    const backups = this._s.backups.map((b) => `<div class="row"><div style="flex:1 1 240px"><b style="font-weight:500">${this.fmtTime(b.created)}</b>
      <div class="muted">${esc(b.reason)} · ${b.users} UserID · ${b.fingerprints} ${this.t("fingerprints")}</div></div>
      <button class="btn" data-act="backup-download" data-n="${esc(b.name)}">${this.t("download")}</button>
      <button class="btn" data-act="backup-restore" data-n="${esc(b.name)}">${this.t("restore")}</button></div>`).join("");
    return `<main><div class="cols"><div class="grow">
      <section class="card tablewrap"><h2>${this.t("action_categories")}</h2><p class="sub">${this.t("categories_help")}</p><p class="sub">${this.t("second_help")}</p>
        <div class="row" style="margin-bottom:12px"><label class="field" style="flex:1 1 320px">${this.t("numbering")}<select id="numbering">
          <option value="category" ${!this.plain() ? "selected" : ""}>${this.t("numbering_category")}</option>
          <option value="plain" ${this.plain() ? "selected" : ""}>${this.t("numbering_plain")}</option></select></label>
          <label class="field">${this.t("within")}<input id="second-window" type="number" min="1" max="10" step="0.5" value="${st?.second_window ?? 3}" style="width:110px"></label></div>
        ${this.plain() ? "" : `<table style="min-width:680px"><thead><tr><th>${this.t("digit")}</th><th>${this.t("type")}</th><th>${this.t("display_name")}</th>
          <th>${this.t("relay")}</th><th>${this.t("second_pres")}</th><th>${this.t("used_by")}</th></tr></thead><tbody>${rows.join("")}</tbody></table>`}
        <div class="actions" style="margin-top:12px"><button class="btn primary" data-act="cats-save">${this.t("save")}</button></div></section>
      <section class="card"><h2>${this.t("backups")}</h2><p class="sub">${this.t("backups_help")}</p>
        <div class="row" style="margin-bottom:10px"><label class="chk"><input type="checkbox" id="bk-change" ${bk.on_change !== false ? "checked" : ""}>${this.t("on_change")}</label>
          <label class="chk"><input type="checkbox" id="bk-nightly" ${bk.nightly !== false ? "checked" : ""}>${this.t("nightly")}</label>
          <label class="row muted">${this.t("keep")}<input id="bk-keep" type="number" min="1" max="500" value="${bk.keep ?? 30}" style="width:80px"></label>
          <button class="btn primary" style="margin-left:auto" data-act="backup-now">${this.t("backup_now")}</button></div>
        <div class="list">${backups}</div>
        <div class="actions" style="justify-content:flex-start;margin-top:10px"><button class="btn" data-act="backup-import">${this.t("import_file")}</button></div></section></div>
      <div class="side"><section class="card"><h2>${this.t("general")}</h2>
        <label class="chk"><input type="checkbox" id="notify-keypad" ${st?.settings?.notify_keypad !== false ? "checked" : ""}>${this.t("notify_keypad")}</label>
        <div class="actions" style="margin-top:12px"><button class="btn primary" data-act="settings-save">${this.t("save")}</button></div></section></div>
      </div></main>`;
  }

  renderModal() {
    const m = this._s.modal;
    if (!m) return "";
    let inner = "";
    const cats = this.cats();
    if (m.kind === "confirm") {
      inner = `<p>${m.text}</p><div class="actions"><button class="btn" data-act="close-modal">${this.t("cancel")}</button>
        <button class="btn danger" data-act="confirm-ok">${this.t("delete")}</button></div>`;
    } else if (m.kind === "person") {
      inner = `<h2>${this.t("new_person_title")}</h2><label class="field">${this.t("name")}<input id="np-name" maxlength="24"></label>
        <label class="field">${this.t("number_auto")}<input id="np-num" type="number" min="1" max="99"></label>
        <div class="actions"><button class="btn" data-act="close-modal">${this.t("cancel")}</button><button class="btn primary" data-act="save-new-person">${this.t("save")}</button></div>`;
    } else if (m.kind === "enroll") {
      const p = this.findPerson(m.person);
      const head = `<div class="row"><h2>${this.t("add_fingerprint")}</h2><span class="muted" style="margin-left:auto">${esc(p?.name || "")} · <b>${esc(this.fingerName(m.physical))}</b></span></div>`;
      if (m.step === "choose") {
        const opts = this.plain() ? `<input type="hidden" name="cat" value="0">` : Object.keys(cats).map((d) =>
          `<label class="chk" style="border:1px solid var(--zk-line);border-radius:10px;padding:6px 12px"><input type="radio" name="cat" value="${d}" ${d === "0" ? "checked" : ""}>${esc(cats[d].name)}</label>`).join("");
        inner = `${head}${this.plain() ? opts : `<b style="font-weight:500">${this.t("category_of_finger")}</b><div class="row">${opts}</div>`}
          <div class="actions"><button class="btn" data-act="close-modal">${this.t("cancel")}</button><button class="btn primary" data-act="enroll-start">${this.t("start")}</button></div>`;
      } else {
        const digits = String(m.user_id).split("").map((c) => `<span class="key big">${c}</span>`).join("");
        const cat = cats[String(m.digit)];
        const status = m.step === "wait" ? `<div class="row"><span class="spin"></span>${this.t("waiting")}</div>`
          : m.step === "done" ? `<div class="row" style="color:#067647;font-weight:500">✓ ${this.t("enrolled")}</div>`
          : `<div class="row" style="color:#b42318">${this.t("timeout")}</div>`;
        inner = `${head}<b style="font-weight:500">${this.t("on_keypad")}</b>
          <div class="row muted"><span class="key">*</span><span class="key">#</span>${this.t("admin_code")} → <span class="key">1</span>${this.t("add")} → ${digits}<span class="key">#</span> → ${this.t("place_finger")}</div>
          ${this.plain() ? "" : `<div class="muted"><span class="mono" style="font-size:20px;font-weight:700;color:var(--zk-accent)">${m.user_id}</span> ${this.t("eq_person_cat", { p: m.person, d: m.digit, c: esc(cat?.name || "") })}</div>`}
          ${cat && !cat.relay ? `<div class="muted">${this.t("no_relay_note", { c: esc(cat.name) })}</div>` : ""}
          ${status}<div class="actions"><button class="btn ${m.step === "done" ? "primary" : ""}" data-act="enroll-cancel">${m.step === "done" ? this.t("done") : this.t("cancel")}</button></div>`;
      }
    } else if (m.kind === "card") {
      const persons = this.persons();
      inner = `<h2>${this.t("learn_card")}</h2>
        ${m.card ? `<div class="detail" style="margin:0"><div><b style="font-weight:500">${this.t("card_detected")}</b><div class="mono" style="font-size:18px">#${m.card}</div>
          <div class="muted">${this.t("unknown_to_device")}</div></div></div>` : `<div class="row muted"><span class="spin"></span>${this.t("card_wait")}</div>`}
        <label class="field">${this.t("enter_card")}<input id="card-no" type="number" min="1" value="${esc(m.cardInput || "")}"></label>
        <div class="row"><label class="field" style="flex:1 1 200px">${this.t("person")}<select id="card-person">${persons.map((p) =>
          `<option value="${p.num}" ${p.num === m.person ? "selected" : ""}>${esc(p.name || p.num)} (${p.num})</option>`).join("")}</select></label>
          ${this.plain() ? `<input type="hidden" id="card-digit" value="0">` : `<label class="field" style="flex:1 1 140px">${this.t("category")}<select id="card-digit">${Object.keys(cats).map((d) =>
            `<option value="${d}" ${Number(d) === m.digit ? "selected" : ""}>${esc(cats[d].name)}</option>`).join("")}</select></label>`}</div>
        <div class="actions"><button class="btn" data-act="close-modal">${this.t("cancel")}</button><button class="btn primary" data-act="card-save">${this.t("write_device")}</button></div>`;
    } else if (m.kind === "code") {
      inner = `<h2>${this.t("code_title", { c: esc(this.catName(m.digit)) })}</h2>
        <label class="field">${this.t("code_label")}<input id="code-val" type="password" inputmode="numeric" pattern="[0-9]{1,6}" maxlength="6" autocomplete="off"></label>
        <div class="actions"><button class="btn" data-act="close-modal">${this.t("cancel")}</button><button class="btn primary" data-act="code-save">${this.t("save")}</button></div>`;
    } else if (m.kind === "assign") {
      inner = `<h2>${this.t("assign_title", { u: m.user_id })}</h2>
        <label class="field">${this.t("existing_person")}<select id="as-person"><option value="">—</option>${this.persons().map((p) =>
          `<option value="${p.num}">${esc(p.name || p.num)} (${p.num})</option>`).join("")}</select></label>
        <label class="field">${this.t("new_number")}<input id="as-new" type="number" min="1" max="99"></label>
        ${this.plain() ? "" : `<label class="field">${this.t("category")}<select id="as-digit">${Object.keys(cats).map((d) => `<option value="${d}">${esc(cats[d].name)}</option>`).join("")}</select></label>`}
        <div class="actions"><button class="btn" data-act="close-modal">${this.t("cancel")}</button><button class="btn primary" data-act="assign-save">${this.t("save")}</button></div>`;
    } else if (m.kind === "restore") {
      inner = `<h2>${this.t("restore_title", { name: esc(m.name) })}</h2>
        <label class="chk"><input type="radio" name="mode" value="merge" checked>${this.t("merge")}</label>
        <label class="chk"><input type="radio" name="mode" value="replace">${this.t("replace_all")}</label>
        <p class="muted">${this.t("restore_help")}</p>
        <div class="actions"><button class="btn" data-act="close-modal">${this.t("cancel")}</button><button class="btn primary" data-act="restore-ok">${this.t("restore")}</button></div>`;
    }
    return `<div class="modal"><div class="dlg" role="dialog" aria-modal="true">${inner}</div></div>`;
  }
}

customElements.define("zkaccess-native-panel", ZkAccessPanel);
