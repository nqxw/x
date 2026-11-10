/**
 * Platform Spoof — ShiggyCord / Vendetta
 * Rewrite gateway IDENTIFY client properties so Discord shows any platform.
 * Devices: desktop, web, console, vr, xbox, playstation, linux, macos, ios, android
 */

const DESKTOP = {
  version: "1.0.9265",
  build: 652418,
  native: 96160,
  electron: "42.11.15",
  chrome: "148.0.7778.280",
  osVersion: "10",
  osSdk: "19045"
};

const DESKTOP_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/" +
  DESKTOP.version +
  " Chrome/" +
  DESKTOP.chrome +
  " Electron/" +
  DESKTOP.electron +
  " Safari/537.36";

const MAC_UA =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) discord/" +
  DESKTOP.version +
  " Chrome/" +
  DESKTOP.chrome +
  " Electron/" +
  DESKTOP.electron +
  " Safari/537.36";

const LINUX_UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) discord/" +
  DESKTOP.version +
  " Chrome/" +
  DESKTOP.chrome +
  " Electron/" +
  DESKTOP.electron +
  " Safari/537.36";

const WEB_UA =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/" +
  DESKTOP.chrome +
  " Safari/537.36";

// Order shown in settings
const DEVICES = [
  "desktop",
  "web",
  "console",
  "vr",
  "xbox",
  "playstation",
  "linux",
  "macos",
  "ios",
  "android"
];

const DEVICE_LABELS = {
  desktop: "Desktop (Windows)",
  web: "Web (Chrome)",
  console: "Console (generic)",
  vr: "VR (Quest / Meta)",
  xbox: "Xbox",
  playstation: "PlayStation",
  linux: "Desktop (Linux)",
  macos: "Desktop (macOS)",
  ios: "iOS",
  android: "Android"
};

let unpatches = [];
let storage;

function getApi() {
  return window.vendetta || window.bunny || window.kettu || {};
}

function findByProps(...props) {
  const api = getApi();
  if (api.metro?.findByProps) return api.metro.findByProps(...props);
  if (api.findByProps) return api.findByProps(...props);
  return null;
}

function patchAfter(obj, key, cb) {
  const api = getApi();
  if (api.patcher?.after) return api.patcher.after(obj, key, cb);
  if (api.after) return api.after(obj, key, cb);
  const orig = obj[key];
  if (typeof orig !== "function") return () => {};
  obj[key] = function (...args) {
    const ret = orig.apply(this, args);
    try {
      return cb.call(this, args, ret);
    } catch (_) {
      return ret;
    }
  };
  return () => {
    obj[key] = orig;
  };
}

function locale() {
  try {
    const i18n = findByProps("locale", "Messages");
    if (i18n?.locale) return i18n.locale;
  } catch (_) {}
  return "en-US";
}

function keepIds(base) {
  return {
    client_launch_id: base?.client_launch_id,
    client_heartbeat_session_id: base?.client_heartbeat_session_id
  };
}

function propertiesFor(kind, base) {
  const keep = keepIds(base);
  const system_locale = base?.system_locale || locale();

  switch (kind) {
    case "web":
      return Object.assign(
        {
          os: "Windows",
          browser: "Chrome",
          device: "",
          system_locale,
          has_client_mods: false,
          browser_user_agent: WEB_UA,
          browser_version: DESKTOP.chrome,
          os_version: "10",
          referrer: "",
          referring_domain: "",
          referrer_current: "",
          referring_domain_current: "",
          release_channel: "stable",
          client_build_number: DESKTOP.build,
          client_event_source: null
        },
        keep
      );

    case "console":
      return Object.assign(
        {
          os: "Xbox",
          browser: "Discord Embedded",
          device: "",
          system_locale,
          has_client_mods: false,
          release_channel: "stable",
          client_build_number: DESKTOP.build,
          client_event_source: null
        },
        keep
      );

    case "xbox":
      return Object.assign(
        {
          os: "Xbox",
          browser: "Discord Embedded",
          device: "Xbox Series X",
          system_locale,
          has_client_mods: false,
          release_channel: "stable",
          client_build_number: DESKTOP.build,
          client_event_source: null
        },
        keep
      );

    case "playstation":
      return Object.assign(
        {
          os: "PlayStation",
          browser: "Discord Embedded",
          device: "PlayStation 5",
          system_locale,
          has_client_mods: false,
          release_channel: "stable",
          client_build_number: DESKTOP.build,
          client_event_source: null
        },
        keep
      );

    case "vr":
      // Meta Quest / Discord VR client identity
      return Object.assign(
        {
          os: "Android",
          browser: "Discord VR",
          device: "Quest 3",
          system_locale,
          has_client_mods: false,
          browser_user_agent:
            "Mozilla/5.0 (Linux; Android 12; Quest 3) AppleWebKit/537.36 (KHTML, like Gecko) OculusBrowser/25.0 Chrome/110.0.0.0 Mobile VR Safari/537.36",
          browser_version: "25.0",
          os_version: "12",
          release_channel: "stable",
          client_build_number: DESKTOP.build,
          client_event_source: null,
          // presence / platform indicator often keys off these
          client_performance_session_id: base?.client_performance_session_id
        },
        keep
      );

    case "linux":
      return Object.assign(
        {
          os: "Linux",
          browser: "Discord Client",
          release_channel: "stable",
          client_version: DESKTOP.version,
          os_version: "6.5.0",
          os_arch: "x64",
          app_arch: "x64",
          system_locale,
          has_client_mods: false,
          browser_user_agent: LINUX_UA,
          browser_version: DESKTOP.electron,
          client_build_number: DESKTOP.build,
          native_build_number: DESKTOP.native,
          client_event_source: null
        },
        keep
      );

    case "macos":
      return Object.assign(
        {
          os: "Mac OS X",
          browser: "Discord Client",
          release_channel: "stable",
          client_version: DESKTOP.version,
          os_version: "23.4.0",
          os_arch: "arm64",
          app_arch: "arm64",
          system_locale,
          has_client_mods: false,
          browser_user_agent: MAC_UA,
          browser_version: DESKTOP.electron,
          client_build_number: DESKTOP.build,
          native_build_number: DESKTOP.native,
          client_event_source: null
        },
        keep
      );

    case "ios":
      return Object.assign(
        {
          os: "iOS",
          browser: "Discord iOS",
          device: "iPhone",
          system_locale,
          has_client_mods: false,
          browser_user_agent:
            "Discord-iOS/254.0 (iPhone; iOS 17.4; Scale/3.00)",
          os_version: "17.4",
          release_channel: "stable",
          client_build_number: 65412,
          client_event_source: null
        },
        keep
      );

    case "android":
      return Object.assign(
        {
          os: "Android",
          browser: "Discord Android",
          device: "Pixel 8",
          system_locale,
          has_client_mods: false,
          browser_user_agent: "Discord-Android/254.0",
          os_version: "14",
          release_channel: "stable",
          client_build_number: 254012,
          client_event_source: null
        },
        keep
      );

    case "desktop":
    default:
      return Object.assign(
        {
          os: "Windows",
          browser: "Discord Client",
          release_channel: "stable",
          client_version: DESKTOP.version,
          os_version: DESKTOP.osVersion,
          os_arch: "x64",
          app_arch: "x64",
          system_locale,
          has_client_mods: false,
          browser_user_agent: DESKTOP_UA,
          browser_version: DESKTOP.electron,
          os_sdk_version: DESKTOP.osSdk,
          client_build_number: DESKTOP.build,
          native_build_number: DESKTOP.native,
          client_event_source: null
        },
        keep
      );
  }
}

function wantedDevice() {
  if (!storage?.enabled) return "";
  const d = storage.device;
  return DEVICES.includes(d) ? d : "desktop";
}

function rewriteIdentify(result) {
  const kind = wantedDevice();
  if (!kind || !result || typeof result !== "object") return result;
  if (!result.properties || typeof result.properties !== "object") return result;
  return Object.assign({}, result, {
    properties: propertiesFor(kind, result.properties)
  });
}

function hookGateway() {
  const candidates = [
    findByProps("handleIdentify", "getSocket"),
    findByProps("handleIdentify"),
    findByProps("identify", "connect"),
    findByProps("socket", "handleIdentify")
  ].filter(Boolean);

  for (const mod of candidates) {
    const target =
      mod && typeof mod.handleIdentify === "function"
        ? mod
        : mod?.socket && typeof mod.socket.handleIdentify === "function"
          ? mod.socket
          : null;
    if (!target) continue;

    const un = patchAfter(target, "handleIdentify", (args, ret) => {
      try {
        if (ret && typeof ret.then === "function") {
          return ret.then(rewriteIdentify);
        }
        return rewriteIdentify(ret);
      } catch (_) {
        return ret;
      }
    });
    unpatches.push(un);
    return true;
  }

  const idMod = findByProps("getIdentify", "identify");
  if (idMod && typeof idMod.getIdentify === "function") {
    const un = patchAfter(idMod, "getIdentify", (args, ret) => rewriteIdentify(ret));
    unpatches.push(un);
    return true;
  }

  // Presence / platform indicator modules sometimes read os from client info
  const clientInfo = findByProps("os", "browser", "client_version");
  if (clientInfo && typeof clientInfo === "object") {
    try {
      const kind = wantedDevice();
      if (kind) {
        const props = propertiesFor(kind, {});
        const un = patchAfter(
          { get: () => clientInfo },
          "get",
          () => Object.assign({}, clientInfo, props)
        );
        // soft — ignore if structure wrong
        if (typeof un === "function") unpatches.push(un);
      }
    } catch (_) {}
  }

  return false;
}

function showToast(content) {
  try {
    const api = getApi();
    if (api.ui?.toasts?.open) api.ui.toasts.open({ content });
    else if (api.toasts?.showToast) api.toasts.showToast(content);
  } catch (_) {}
}

export default {
  onLoad() {
    const api = getApi();
    storage = api.plugin?.storage;
    if (!storage || typeof storage !== "object") {
      storage = { enabled: true, device: "desktop" };
    }
    if (storage.enabled === undefined) storage.enabled = true;
    if (!DEVICES.includes(storage.device)) storage.device = "desktop";

    const ok = hookGateway();
    if (!ok) {
      setTimeout(() => {
        if (unpatches.length === 0) hookGateway();
      }, 4000);
    }
  },

  onUnload() {
    for (const u of unpatches) {
      try {
        u();
      } catch (_) {}
    }
    unpatches = [];
  }
};

export function getSettings() {
  const React = window.React || getApi().metro?.common?.React;
  const Forms = getApi().metro?.common?.Forms || {};
  const { FormSection, FormSwitch, FormRadio, FormRow } = Forms;

  if (!React || !FormSection) return () => null;

  return () => {
    const s = storage || {};
    const current = s.device || "desktop";
    return React.createElement(
      FormSection,
      { title: "Platform Spoof" },
      React.createElement(FormSwitch, {
        label: "Enabled",
        subLabel: "Rewrite gateway IDENTIFY client properties",
        value: !!s.enabled,
        onValueChange: (v) => {
          s.enabled = v;
          showToast(v ? "Platform spoof on — reconnect for full effect" : "Platform spoof off");
        }
      }),
      React.createElement(FormRow, {
        label: "Current",
        subLabel: DEVICE_LABELS[current] || current
      }),
      ...DEVICES.map((d) =>
        React.createElement(FormRadio, {
          key: d,
          selected: current === d,
          label: DEVICE_LABELS[d] || d,
          onPress: () => {
            s.device = d;
            showToast("Show as " + (DEVICE_LABELS[d] || d) + " — reconnect gateway");
          }
        })
      )
    );
  };
}
