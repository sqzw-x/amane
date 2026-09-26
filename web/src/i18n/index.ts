/**
 * i18next initialization - imported before React renders.
 *
 * All translations are bundled inline (no lazy loading) since the
 * total corpus is small across 2 languages.
 */

import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";
// English namespaces
import enAgent from "./locales/en/agent.json";
import enCommon from "./locales/en/common.json";
import enNetworkCheck from "./locales/en/networkCheck.json";
import enFeeds from "./locales/en/feeds.json";
import enFileBrowser from "./locales/en/fileBrowser.json";
import enLibrary from "./locales/en/library.json";
import enLogs from "./locales/en/logs.json";
import enMetadata from "./locales/en/metadata.json";
import enPlugins from "./locales/en/plugins.json";
import enSchedules from "./locales/en/schedules.json";
import enSettings from "./locales/en/settings.json";
import enTasks from "./locales/en/tasks.json";
// Chinese (Simplified) namespaces - source language
import zhCNAgent from "./locales/zh-CN/agent.json";
import zhCNCommon from "./locales/zh-CN/common.json";
import zhCNNetworkCheck from "./locales/zh-CN/networkCheck.json";
import zhCNFeeds from "./locales/zh-CN/feeds.json";
import zhCNFileBrowser from "./locales/zh-CN/fileBrowser.json";
import zhCNLibrary from "./locales/zh-CN/library.json";
import zhCNLogs from "./locales/zh-CN/logs.json";
import zhCNMetadata from "./locales/zh-CN/metadata.json";
import zhCNPlugins from "./locales/zh-CN/plugins.json";
import zhCNSchedules from "./locales/zh-CN/schedules.json";
import zhCNSettings from "./locales/zh-CN/settings.json";
import zhCNTasks from "./locales/zh-CN/tasks.json";

export const defaultNS = "common";
export const supportedLanguages = ["zh-CN", "en"] as const;

export const resources = {
  "zh-CN": {
    common: zhCNCommon,
    networkCheck: zhCNNetworkCheck,
    feeds: zhCNFeeds,
    fileBrowser: zhCNFileBrowser,
    library: zhCNLibrary,
    metadata: zhCNMetadata,
    plugins: zhCNPlugins,
    tasks: zhCNTasks,
    settings: zhCNSettings,
    schedules: zhCNSchedules,
    logs: zhCNLogs,
    agent: zhCNAgent,
  },
  en: {
    common: enCommon,
    networkCheck: enNetworkCheck,
    feeds: enFeeds,
    fileBrowser: enFileBrowser,
    library: enLibrary,
    metadata: enMetadata,
    plugins: enPlugins,
    tasks: enTasks,
    settings: enSettings,
    schedules: enSchedules,
    logs: enLogs,
    agent: enAgent,
  },
} as const;

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    defaultNS,
    fallbackLng: "zh-CN",
    supportedLngs: [...supportedLanguages],

    interpolation: {
      escapeValue: false,
    },

    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "amane-web-language",
      caches: ["localStorage"],
    },
  });

export default i18n;
