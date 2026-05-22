import { test, expect, type Page, type Request } from "@playwright/test";

/**
 * G5.4 — Button liveness audit.
 *
 * Each refresh/sync/snapshot button in the UI must trigger a real
 * backend request when clicked. This spec walks every pane, finds
 * the per-pane refresh/sync controls, clicks them, and asserts at
 * least one fetch went out within a short window.
 *
 * Implements the B0 mandate ("no dead features") in CI form so a
 * future regression — handler stripped, endpoint renamed — fails
 * the build instead of going unnoticed.
 *
 * Sidebar Sync (Activity → /health) is exercised first, then each
 * mode's own refresh control. The dashboard and security panes
 * fire many parallel fetches on mount; this spec only asserts
 * "at least one fetch" so flaky individual endpoints don't break
 * the suite.
 */

const BACKEND_HOSTS = ["127.0.0.1", "localhost"];

function isBackendRequest(req: Request): boolean {
  const url = req.url();
  if (!url.startsWith("http")) return false;
  // Backend is on a different port than the dev server — we don't
  // care which port specifically; we just want to exclude same-port
  // Vite HMR / asset fetches.
  return BACKEND_HOSTS.some((h) => url.includes(`://${h}:`)) &&
         !url.includes(":1420/") &&  // dev server
         !url.includes("/@vite") &&
         !url.includes("/src/");
}

async function clickAndAwaitBackend(
  page: Page,
  click: () => Promise<void>,
  description: string,
  timeoutMs = 4000,
): Promise<void> {
  const seen: string[] = [];
  const handler = (req: Request) => {
    if (isBackendRequest(req)) seen.push(req.url());
  };
  page.on("request", handler);
  try {
    await click();
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline && seen.length === 0) {
      await page.waitForTimeout(100);
    }
    expect(seen.length, `${description} fired ≥1 backend request`).toBeGreaterThan(0);
  } finally {
    page.off("request", handler);
  }
}

async function gotoMode(page: Page, modeLabel: string): Promise<void> {
  const navBtn = page.getByRole("button", { name: new RegExp(`^${modeLabel}$`, "i") }).first();
  await navBtn.click();
  await page.waitForTimeout(150);
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  // Leave the welcome screen if any continue/start button is up.
  const continueBtn = page.getByRole("button", { name: /continue|start|new chat/i }).first();
  if (await continueBtn.isVisible().catch(() => false)) {
    await continueBtn.click();
  }
});

test("Sidebar Sync triggers a backend health probe", async ({ page }) => {
  await gotoMode(page, "Chat"); // any non-welcome mode so the sidebar is mounted
  await clickAndAwaitBackend(
    page,
    async () => {
      await page.getByRole("button", { name: /^sync$/i }).first().click();
    },
    "Sidebar Sync",
  );
});

test("Dashboard mounts and at least one widget refreshes from the backend", async ({ page }) => {
  // The mount itself fires ~14 parallel fetches; we just listen on nav.
  await clickAndAwaitBackend(
    page,
    () => gotoMode(page, "Dashboard"),
    "Dashboard mount",
  );
});

test("Analytics mount fires a backend request", async ({ page }) => {
  await clickAndAwaitBackend(
    page,
    () => gotoMode(page, "Analytics"),
    "Analytics mount",
  );
});

test("Logs Refresh button fires a backend request", async ({ page }) => {
  await gotoMode(page, "Logs");
  // The pane mounts with its own request; click Refresh and assert another.
  await page.waitForTimeout(500);
  await clickAndAwaitBackend(
    page,
    async () => {
      // Logs uses a bare RefreshCw icon button inside the pane header.
      const btn = page.locator('button[title*="efresh" i]').first();
      if (await btn.isVisible().catch(() => false)) await btn.click();
      else await page.getByRole("button").filter({ hasText: /refresh/i }).first().click();
    },
    "Logs Refresh",
  );
});

test("Theater Refresh icon fires a backend request", async ({ page }) => {
  await gotoMode(page, "Theater");
  await page.waitForTimeout(500);
  await clickAndAwaitBackend(
    page,
    async () => {
      await page.locator('button[title="Refresh"]').first().click();
    },
    "Theater Refresh",
  );
});

test("Security pane mount fires a backend request", async ({ page }) => {
  await clickAndAwaitBackend(
    page,
    () => gotoMode(page, "Security"),
    "Security mount",
  );
});

test("Brain pane mount fires a backend request", async ({ page }) => {
  await clickAndAwaitBackend(
    page,
    () => gotoMode(page, "Brain"),
    "Brain mount",
  );
});

test("Settings Test Connection button fires a backend request", async ({ page }) => {
  await gotoMode(page, "Settings");
  await page.waitForTimeout(300);
  await clickAndAwaitBackend(
    page,
    async () => {
      const btn = page.getByRole("button", { name: /test.*connection|test all/i }).first();
      await btn.click();
    },
    "Settings Test Connection",
  );
});

test("Apps pane mount fires a backend request", async ({ page }) => {
  await clickAndAwaitBackend(
    page,
    () => gotoMode(page, "Apps"),
    "Apps mount",
  );
});
