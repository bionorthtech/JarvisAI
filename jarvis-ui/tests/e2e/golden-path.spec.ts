import { test, expect } from "@playwright/test";

/**
 * Golden path: cold start → all 9 modes reachable → settings opens → theme switches.
 * Phase 7.4 baseline test — extend with per-mode specs in this folder.
 */

const MODES = [
  "chat",
  "coder",
  "terminal",
  "dashboard",
  "analytics",
  "logs",
  "apps",
  "security",
  "brain",
] as const;

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  // Welcome screen → enter the app. Look for any "Continue" / "Start" button or
  // simply navigate past it by clicking the first available session row.
  const continueBtn = page.getByRole("button", { name: /continue|start|new chat/i });
  if (await continueBtn.first().isVisible().catch(() => false)) {
    await continueBtn.first().click();
  }
});

test("every UI mode is reachable from the navigation rail", async ({ page }) => {
  for (const mode of MODES) {
    const navItem = page.getByRole("button", { name: new RegExp(mode, "i") }).first();
    await expect(navItem, `nav item for ${mode}`).toBeVisible();
    await navItem.click();
    await page.waitForTimeout(150);
  }
});

test("settings opens and theme picker is visible", async ({ page }) => {
  await page.getByRole("button", { name: /settings/i }).first().click();
  await expect(page.getByText(/theme/i).first()).toBeVisible();
});

test("theme switcher cycles every theme (apple + 6 dark accents)", async ({ page }) => {
  await page.getByRole("button", { name: /settings/i }).first().click();
  // Apple Dark first (B2 default), then the 6 accent themes.
  for (const theme of ["apple", "amber", "cyan", "green", "violet", "rose", "blue"]) {
    const btn = page.getByRole("button", { name: new RegExp(theme, "i") });
    if (await btn.first().isVisible().catch(() => false)) {
      await btn.first().click();
      await page.waitForTimeout(80);
    }
  }
});

/**
 * A7.2 — per-mode × per-theme smoke matrix.
 *
 * Walks {apple, amber, cyan, green, violet, rose, blue} × every mode
 * and asserts no console errors fire while switching. Catches React
 * rendering issues that surface only under specific theme/mode pairs
 * (e.g. missing token, undefined accent class).
 */
test("every theme renders every mode without console errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });

  for (const theme of ["apple", "amber", "cyan", "green", "violet", "rose", "blue"]) {
    // Open Settings and pick the theme.
    await page.getByRole("button", { name: /settings/i }).first().click();
    const themeBtn = page.getByRole("button", { name: new RegExp(theme, "i") }).first();
    if (await themeBtn.isVisible().catch(() => false)) {
      await themeBtn.click();
      await page.waitForTimeout(60);
    }
    // Touch every mode briefly.
    for (const mode of MODES) {
      const navItem = page.getByRole("button", { name: new RegExp(`^${mode}$`, "i") }).first();
      if (await navItem.isVisible().catch(() => false)) {
        await navItem.click();
        await page.waitForTimeout(60);
      }
    }
  }
  // Allow benign network-error noise (backend may not be fully up); fail
  // only on React/TS-level errors.
  const fatal = errors.filter((e) =>
    !e.includes("Failed to fetch") &&
    !e.includes("NetworkError") &&
    !e.includes("net::ERR_") &&
    !e.includes("favicon"));
  expect(fatal, `theme×mode matrix should not error: ${fatal.join("\n")}`).toEqual([]);
});
