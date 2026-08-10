"use client";

import { useSyncExternalStore } from "react";

/** The three states a viewer can choose between. */
export type ThemeChoice = "light" | "dark" | "system";

const STORAGE_KEY = "helios-theme";

/**
 * Script injected before first paint to stamp the stored theme onto <html>.
 *
 * Without this the page renders in the default theme and then corrects itself once React
 * hydrates, producing a visible flash -- worst for someone who chose dark, since they get a
 * full white screen first.
 *
 * Deliberately dependency-free and synchronous: it must run before the browser paints.
 */
export const THEME_INIT_SCRIPT = `
(function () {
  try {
    var stored = localStorage.getItem('${STORAGE_KEY}');
    if (stored === 'light' || stored === 'dark') {
      document.documentElement.setAttribute('data-theme', stored);
    }
  } catch (e) {
    /* private browsing can throw on localStorage; the default theme is a fine fallback */
  }
})();
`;

/**
 * Apply a theme choice to the document and remember it.
 *
 * "system" removes the attribute entirely rather than resolving it to a concrete value, so the
 * page keeps following the OS preference when it changes rather than freezing at whatever it
 * happened to be at the moment of choosing.
 *
 * @param choice - The theme to apply.
 */
function applyTheme(choice: ThemeChoice): void {
  const root = document.documentElement;

  if (choice === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", choice);
  }

  try {
    if (choice === "system") {
      localStorage.removeItem(STORAGE_KEY);
    } else {
      localStorage.setItem(STORAGE_KEY, choice);
    }
  } catch {
    /* storage unavailable; the choice still applies for this page view */
  }
}

/** Read the stored choice, defaulting to following the system. */
function storedChoice(): ThemeChoice {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

const CHANGE_EVENT = "helios-theme-change";

/**
 * Subscribe to theme changes.
 *
 * Listens for the `storage` event as well as our own, so the control stays in sync when the
 * theme is changed in another tab.
 */
function subscribe(onChange: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, onChange);
  window.addEventListener("storage", onChange);

  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

/** The theme the server renders with; it cannot read localStorage. */
function serverSnapshot(): ThemeChoice {
  return "system";
}

const OPTIONS: { value: ThemeChoice; label: string; icon: string }[] = [
  { value: "light", label: "Light", icon: "☀" },
  { value: "system", label: "System", icon: "◐" },
  { value: "dark", label: "Dark", icon: "☾" },
];

/**
 * Three-way theme selector: light, system, dark.
 *
 * System is offered as a first-class option rather than being implied by "not chosen", because a
 * viewer who wants the page to track their OS at sunset needs a way to return to that after
 * trying a fixed theme.
 */
export function ThemeToggle() {
  // The theme lives outside React -- in localStorage and on the <html> element, where a
  // pre-paint script already applied it. useSyncExternalStore is the supported way to read such
  // state: it handles the server/client mismatch without a state write inside an effect.
  const choice = useSyncExternalStore(subscribe, storedChoice, serverSnapshot);

  function select(next: ThemeChoice) {
    applyTheme(next);
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }

  return (
    <div
      className="inline-flex items-center gap-0.5 rounded-md border border-edge p-0.5"
      role="group"
      aria-label="Colour theme"
    >
      {OPTIONS.map((option) => {
        const active = choice === option.value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => select(option.value)}
            aria-pressed={active}
            title={`${option.label} theme`}
            className={`rounded px-2 py-1 text-xs transition-colors ${
              active
                ? "bg-accent text-accent-contrast"
                : "text-faint hover:text-foreground"
            }`}
          >
            <span aria-hidden>{option.icon}</span>
            <span className="sr-only">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}
