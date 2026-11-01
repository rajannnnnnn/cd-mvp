import { Page, Locator, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Common (reusable) functions. Components call these instead of raw Playwright,
 * so waiting / logging / retry behaviour lives in ONE place.
 */
export class CommonFunctions {
  constructor(private readonly page: Page) {}

  async navigateTo(url: string): Promise<void> {
    await this.page.goto(url, { waitUntil: 'domcontentloaded' });
  }

  async click(locator: Locator): Promise<void> {
    await locator.waitFor({ state: 'visible' });
    await locator.click();
  }

  async type(locator: Locator, text: string): Promise<void> {
    await locator.waitFor({ state: 'visible' });
    await locator.fill(text);
  }

  async getText(locator: Locator): Promise<string> {
    await locator.waitFor({ state: 'visible' });
    return ((await locator.textContent()) ?? '').trim();
  }

  async isVisible(locator: Locator): Promise<boolean> {
    return locator.isVisible();
  }

  async selectOption(locator: Locator, value: string): Promise<void> {
    await locator.waitFor({ state: 'visible' });
    await locator.selectOption(value);
  }

  async expectVisible(locator: Locator, message?: string): Promise<void> {
    await expect(locator, message).toBeVisible();
  }

  async expectText(locator: Locator, expected: string | RegExp): Promise<void> {
    await expect(locator).toContainText(expected);
  }

  async expectUrlContains(fragment: string): Promise<void> {
    await expect(this.page).toHaveURL(new RegExp(escapeRegExp(fragment)));
  }

  async takeScreenshot(name: string, dir: string): Promise<Buffer> {
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `${sanitize(name)}-${timestamp()}.png`);
    return this.page.screenshot({ path: file, fullPage: true });
  }
}

export const sanitize = (s: string): string => s.replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '');
export const timestamp = (): string => new Date().toISOString().replace(/[:.]/g, '-');
export const escapeRegExp = (s: string): string => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
export const randomString = (len = 8): string =>
  Math.random().toString(36).slice(2, 2 + len).padEnd(len, 'x');
export const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));
