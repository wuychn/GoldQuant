(function (global) {
  var cookieStore = {};

  function serializeCookies() {
    return Object.keys(cookieStore)
      .map(function (k) {
        return k + "=" + cookieStore[k];
      })
      .join("; ");
  }

  var locationObj = {
    href: "https://www.iwencai.com/unifiedwap/home/index",
    hostname: "www.iwencai.com",
    host: "www.iwencai.com",
    protocol: "https:",
    pathname: "/unifiedwap/home/index",
    hash: "",
    search: "",
  };

  var bodyNode = {
    clientWidth: 1920,
    clientHeight: 1080,
    appendChild: function () {},
    addEventListener: function () {},
  };

  var documentObj = {
    body: bodyNode,
    documentElement: { clientWidth: 1920, clientHeight: 1080 },
    createElement: function () {
      return {
        style: {},
        width: 0,
        height: 0,
        src: "",
        href: "",
        target: "_self",
        rel: "",
        onload: null,
        onerror: null,
        onreadystatechange: null,
        readyState: 4,
        appendChild: function () {},
        setAttribute: function () {},
        getAttribute: function () {
          return null;
        },
        getContext: function () {
          return null;
        },
        toDataURL: function () {
          return "";
        },
      };
    },
    getElementsByTagName: function () {
      return [{ target: "_self" }];
    },
    addEventListener: function () {},
    createEvent: function () {
      return { initEvent: function () {} };
    },
    location: locationObj,
  };

  Object.defineProperty(documentObj, "cookie", {
    configurable: true,
    enumerable: true,
    get: function () {
      return serializeCookies();
    },
    set: function (val) {
      if (!val) {
        return;
      }
      var parts = String(val).split(";");
      var pair = parts[0].split("=");
      var name = pair[0].trim();
      var value = pair.slice(1).join("=").trim();
      if (!name) {
        return;
      }
      if (!value || /expires=Thu, 01 Jan 1970/i.test(val)) {
        delete cookieStore[name];
      } else {
        cookieStore[name] = value;
      }
    },
  });

  var navigatorObj = {
    userAgent:
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    language: "zh-CN",
    browserLanguage: "zh-CN",
    platform: "Win32",
    plugins: [],
    cookieEnabled: true,
    appVersion:
      "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
  };

  global.window = global;
  global.self = global;
  global.top = global;
  global.parent = global;
  global.document = documentObj;
  global.navigator = navigatorObj;
  global.location = locationObj;
  global.screen = { width: 1920, height: 1080 };
  global.history = { length: 1 };
  global.localStorage = {
    getItem: function () {
      return null;
    },
    setItem: function () {},
  };
  global.sessionStorage = global.localStorage;
  global.Image = function () {
    this.src = "";
  };
  global.XMLHttpRequest = function () {
    this.open = function () {};
    this.send = function () {};
    this.setRequestHeader = function () {};
    this.readyState = 4;
    this.status = 200;
    this.responseText = "";
    this.getAllResponseHeaders = function () {
      return "";
    };
    this.getResponseHeader = function () {
      return null;
    };
  };
  global.ActiveXObject = undefined;
  global.Element = function () {};
  global.Element.prototype = {
    addEventListener: function () {},
    setAttribute: function () {},
  };
  global.Headers = function (init) {
    this._map = init || {};
    this.set = function (k, v) {
      this._map[k] = v;
    };
    this.get = function (k) {
      return this._map[k];
    };
  };
  global.fetch = function () {
    return Promise.resolve({ ok: true, json: function () { return Promise.resolve({}); } });
  };
  global.setInterval = function () {
    return 1;
  };
  global.clearInterval = function () {};
  global.setTimeout = function (fn) {
    if (typeof fn === "function") {
      fn();
    }
    return 1;
  };
  global.clearTimeout = function () {};
  global.addEventListener = function () {};
  global.encodeURIComponent = encodeURIComponent;
  global.decodeURIComponent = decodeURIComponent;

  global.__getIwencaiCookieV = function () {
    return cookieStore.v || "";
  };
})(this);
