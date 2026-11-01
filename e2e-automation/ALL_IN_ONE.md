# Complete framework in ONE file (copy each block to its path)

Install: `npm i -D typescript@5.9 ts-node @types/node @cucumber/cucumber @playwright/test dotenv`

## `package.json`
```json
{
  "name": "e2e-automation",
  "version": "1.0.0",
  "private": true,
  "description": "Playwright + Cucumber + TypeScript BDD automation framework",
  "scripts": {
    "test": "cucumber-js",
    "test:headed": "HEADLESS=false cucumber-js",
    "test:smoke": "cucumber-js --tags \"@smoke\"",
    "test:firefox": "BROWSER=firefox cucumber-js",
    "test:webkit": "BROWSER=webkit cucumber-js",
    "typecheck": "tsc --noEmit",
    "install:browsers": "playwright install chromium firefox webkit"
  },
  "devDependencies": {
    "@cucumber/cucumber": "^13.3.0",
    "@playwright/test": "^1.64.1",
    "@types/node": "^26.6.6",
    "dotenv": "^18.0.9",
    "ts-node": "^10.9.2",
    "typescript": "5.9"
  }
}
```

## `tsconfig.json`
```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "commonjs",
    "moduleResolution": "node",
    "lib": ["ES2020", "DOM"],
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "forceConsistentCasingInFileNames": true,
    "outDir": "dist",
    "rootDir": "."
  },
  "include": ["src/**/*.ts", "cucumber.js"],
  "exclude": ["node_modules", "dist"]
}
```

## `cucumber.js`
```javascript
// Cucumber configuration. Run:  npx cucumber-js            (all)
//                               npx cucumber-js --tags @smoke
//                               npx cucumber-js --profile ci
const common = {
  paths: ['src/features/**/*.feature'],
  requireModule: ['ts-node/register'],
  require: ['src/support/world.ts', 'src/support/setup.ts', 'src/support/hooks.ts', 'src/steps/**/*.ts'],
  format: [
    'progress-bar',
    'html:reports/cucumber-report.html',
    'json:reports/cucumber-report.json',
  ],
  formatOptions: { snippetInterface: 'async-await' },
  publishQuiet: true,
};

module.exports = {
  default: { ...common },
  // `--profile ci` : no progress bar, retry flaky scenarios once, run in parallel
  ci: {
    ...common,
    format: ['summary', 'html:reports/cucumber-report.html', 'json:reports/cucumber-report.json'],
    retry: 1,
    parallel: 2,
  },
};
```

## `.env.example`
```bash
# ---- Copy this file to .env and adjust ----
# Application under test
BASE_URL=https://www.saucedemo.com

# Browser: chromium | firefox | webkit
BROWSER=chromium
# true = no UI, false = watch the browser
HEADLESS=true
# Slow every action by N ms (useful for debugging, 0 = off)
SLOW_MO=0
# Optional: path to an already-installed browser binary (leave empty to use Playwright's own)
BROWSER_EXECUTABLE_PATH=

# Timeouts (ms)
DEFAULT_TIMEOUT=15000
NAVIGATION_TIMEOUT=30000

# Viewport
VIEWPORT_WIDTH=1366
VIEWPORT_HEIGHT=768

# Artifacts: on | off | only-on-failure
SCREENSHOT=only-on-failure
VIDEO=off
TRACE=only-on-failure

# Test data (do NOT commit real credentials)
VALID_USERNAME=standard_user
VALID_PASSWORD=secret_sauce
LOCKED_USERNAME=locked_out_user
```

## `.gitignore`
```bash
node_modules/
reports/
screenshots/
dist/
.env
```

## `src/features/login.feature`
```gherkin
@login
Feature: Login and shopping basics
  As a shopper
  I want to log in and add products to my cart
  So that I can buy what I need

  Background:
    Given the user is on the login page

  @smoke
  Scenario: Successful login with valid credentials
    When the user logs in with the valid credentials
    Then the products page is displayed
    And 6 products are listed

  @negative
  Scenario: Locked out user cannot log in
    When the user logs in as the locked out user
    Then the error message "Sorry, this user has been locked out" is shown

  @negative
  Scenario Outline: Invalid credentials are rejected
    When the user logs in with username "<username>" and password "<password>"
    Then the error message "<error>" is shown

    Examples:
      | username      | password     | error                                       |
      | standard_user | wrong_pass   | Username and password do not match          |
      | not_a_user    | secret_sauce | Username and password do not match          |
      |               | secret_sauce | Username is required                         |
      | standard_user |              | Password is required                         |

  @smoke @cart
  Scenario: Add a product to the cart
    Given the user is logged in
    When the user adds "Sauce Labs Backpack" to the cart
    Then the cart badge shows 1
```

## `src/steps/login.steps.ts`
```typescript
import { Given, When, Then } from '@cucumber/cucumber';
import { expect } from '@playwright/test';
import { CustomWorld } from '../support/world';
import { ENV } from '../config/env';

Given('the user is on the login page', async function (this: CustomWorld) {
  await this.loginComponent.open();
});

Given('the user is logged in', async function (this: CustomWorld) {
  await this.loginComponent.login(ENV.users.validUsername, ENV.users.validPassword);
  await this.inventoryComponent.expectLoaded();
});

When('the user logs in with the valid credentials', async function (this: CustomWorld) {
  await this.loginComponent.login(ENV.users.validUsername, ENV.users.validPassword);
});

When('the user logs in as the locked out user', async function (this: CustomWorld) {
  await this.loginComponent.login(ENV.users.lockedUsername, ENV.users.validPassword);
});

When(
  'the user logs in with username {string} and password {string}',
  async function (this: CustomWorld, username: string, password: string) {
    await this.loginComponent.login(username, password);
  },
);

When('the user adds {string} to the cart', async function (this: CustomWorld, product: string) {
  await this.inventoryComponent.addItemToCart(product);
});

Then('the products page is displayed', async function (this: CustomWorld) {
  await this.inventoryComponent.expectLoaded();
});

Then('{int} products are listed', async function (this: CustomWorld, expected: number) {
  expect(await this.inventoryComponent.itemCount()).toBe(expected);
});

Then('the error message {string} is shown', async function (this: CustomWorld, text: string) {
  await this.loginComponent.expectError(text);
});

Then('the cart badge shows {int}', async function (this: CustomWorld, count: number) {
  await this.inventoryComponent.expectCartCount(count);
});
```

## `src/components/login.component.ts`
```typescript
import { Page, Locator } from '@playwright/test';
import { CommonFunctions } from '../utils/commonFunctions';

/** Component (page-object) for the login screen: locators + actions, no assertions logic leaks to steps. */
export class LoginComponent {
  private readonly usernameInput: Locator;
  private readonly passwordInput: Locator;
  private readonly loginButton: Locator;
  private readonly errorMessage: Locator;

  constructor(private readonly page: Page, private readonly common: CommonFunctions) {
    this.usernameInput = page.locator('[data-test="username"]');
    this.passwordInput = page.locator('[data-test="password"]');
    this.loginButton = page.locator('[data-test="login-button"]');
    this.errorMessage = page.locator('[data-test="error"]');
  }

  async open(): Promise<void> {
    await this.common.navigateTo('/');
    await this.common.expectVisible(this.loginButton, 'Login page did not load');
  }

  async login(username: string, password: string): Promise<void> {
    await this.common.type(this.usernameInput, username);
    await this.common.type(this.passwordInput, password);
    await this.common.click(this.loginButton);
  }

  async expectError(text: string): Promise<void> {
    await this.common.expectVisible(this.errorMessage);
    await this.common.expectText(this.errorMessage, text);
  }
}
```

## `src/components/inventory.component.ts`
```typescript
import { Page, Locator } from '@playwright/test';
import { CommonFunctions } from '../utils/commonFunctions';

/** Component for the products page that appears after a successful login. */
export class InventoryComponent {
  private readonly title: Locator;
  private readonly items: Locator;
  private readonly cartBadge: Locator;

  constructor(private readonly page: Page, private readonly common: CommonFunctions) {
    this.title = page.locator('[data-test="title"]');
    this.items = page.locator('[data-test="inventory-item"]');
    this.cartBadge = page.locator('[data-test="shopping-cart-badge"]');
  }

  async expectLoaded(): Promise<void> {
    await this.common.expectUrlContains('/inventory.html');
    await this.common.expectText(this.title, 'Products');
  }

  async itemCount(): Promise<number> {
    await this.items.first().waitFor({ state: 'visible' });
    return this.items.count();
  }

  async addItemToCart(productName: string): Promise<void> {
    const item = this.items.filter({ hasText: productName });
    await this.common.click(item.getByRole('button', { name: /add to cart/i }));
  }

  async expectCartCount(count: number): Promise<void> {
    await this.common.expectText(this.cartBadge, String(count));
  }
}
```

## `src/utils/commonFunctions.ts`
```typescript
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
```

## `src/config/env.ts`
```typescript
import * as dotenv from 'dotenv';
import * as path from 'path';

// Load .env from the project root (works no matter where the process is started)
dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const str = (key: string, fallback: string): string => process.env[key]?.trim() || fallback;
const num = (key: string, fallback: number): number => {
  const n = Number(process.env[key]);
  return Number.isFinite(n) && process.env[key] !== '' && process.env[key] !== undefined ? n : fallback;
};
const bool = (key: string, fallback: boolean): boolean => {
  const v = process.env[key]?.trim().toLowerCase();
  if (v === undefined || v === '') return fallback;
  return ['true', '1', 'yes', 'on'].includes(v);
};

export type BrowserName = 'chromium' | 'firefox' | 'webkit';
export type ArtifactMode = 'on' | 'off' | 'only-on-failure';

const browser = str('BROWSER', 'chromium').toLowerCase();
if (!['chromium', 'firefox', 'webkit'].includes(browser)) {
  throw new Error(`Invalid BROWSER="${browser}". Use chromium | firefox | webkit`);
}

const artifact = (key: string, fallback: ArtifactMode): ArtifactMode => {
  const v = str(key, fallback);
  if (!['on', 'off', 'only-on-failure'].includes(v)) {
    throw new Error(`Invalid ${key}="${v}". Use on | off | only-on-failure`);
  }
  return v as ArtifactMode;
};

export const ENV = {
  baseUrl: str('BASE_URL', 'https://www.saucedemo.com'),
  browser: browser as BrowserName,
  headless: bool('HEADLESS', true),
  slowMo: num('SLOW_MO', 0),
  executablePath: process.env.BROWSER_EXECUTABLE_PATH?.trim() || undefined,
  defaultTimeout: num('DEFAULT_TIMEOUT', 15000),
  navigationTimeout: num('NAVIGATION_TIMEOUT', 30000),
  viewport: { width: num('VIEWPORT_WIDTH', 1366), height: num('VIEWPORT_HEIGHT', 768) },
  screenshot: artifact('SCREENSHOT', 'only-on-failure'),
  video: artifact('VIDEO', 'off'),
  trace: artifact('TRACE', 'only-on-failure'),
  users: {
    validUsername: str('VALID_USERNAME', 'standard_user'),
    validPassword: str('VALID_PASSWORD', 'secret_sauce'),
    lockedUsername: str('LOCKED_USERNAME', 'locked_out_user'),
  },
  dirs: {
    reports: path.resolve(__dirname, '../../reports'),
    screenshots: path.resolve(__dirname, '../../screenshots'),
    videos: path.resolve(__dirname, '../../reports/videos'),
    traces: path.resolve(__dirname, '../../reports/traces'),
  },
};
```

## `src/support/setup.ts`
```typescript
import { setDefaultTimeout, BeforeAll, AfterAll } from '@cucumber/cucumber';
import * as fs from 'fs';
import { ENV } from '../config/env';
import { closeBrowser } from './browserInstance';

/**
 * Global setup / teardown (runs once per worker, not per scenario).
 * - cucumber's own step timeout must be larger than Playwright's timeouts
 * - make sure the output folders exist
 */
setDefaultTimeout(ENV.navigationTimeout + 30_000);

BeforeAll(async function () {
  Object.values(ENV.dirs).forEach((dir) => fs.mkdirSync(dir, { recursive: true }));
  console.log(
    `\n▶ Browser: ${ENV.browser} | headless: ${ENV.headless} | base URL: ${ENV.baseUrl}\n`,
  );
});

AfterAll(async function () {
  await closeBrowser();
});
```

## `src/support/hooks.ts`
```typescript
import { Before, After, BeforeStep, Status, ITestCaseHookParameter } from '@cucumber/cucumber';
import * as path from 'path';
import { CustomWorld } from './world';
import { createContext, createPage } from './browserInstance';
import { ENV } from '../config/env';
import { sanitize, timestamp } from '../utils/commonFunctions';

// Fresh, isolated browser context + page for every scenario
Before(async function (this: CustomWorld, { pickle }: ITestCaseHookParameter) {
  this.scenarioName = pickle.name;
  this.context = await createContext(pickle.name);
  this.page = await createPage(this.context);
});

// Optional: tag a scenario with @skip-login-check etc. later via tagged hooks
BeforeStep(async function (this: CustomWorld) {
  // place for per-step logging if needed
});

After(async function (this: CustomWorld, { pickle, result }: ITestCaseHookParameter) {
  const failed = result?.status === Status.FAILED;
  const name = sanitize(pickle.name);

  // Screenshot -> attached to the HTML/JSON report
  if (ENV.screenshot === 'on' || (ENV.screenshot === 'only-on-failure' && failed)) {
    const png = await this.common.takeScreenshot(name, ENV.dirs.screenshots);
    this.attach(png, 'image/png');
  }

  // Trace (open with: npx playwright show-trace reports/traces/<file>.zip)
  if (ENV.trace !== 'off') {
    if (ENV.trace === 'on' || failed) {
      await this.context.tracing.stop({
        path: path.join(ENV.dirs.traces, `${name}-${timestamp()}.zip`),
      });
    } else {
      await this.context.tracing.stop();
    }
  }

  const video = this.page.video();
  await this.page.close();
  await this.context.close(); // flushes the video file
  if (video && ENV.video === 'only-on-failure' && !failed) {
    await video.delete().catch(() => undefined);
  }
});
```

## `src/support/browserInstance.ts`
```typescript
import { Browser, BrowserContext, Page, chromium, firefox, webkit, BrowserType } from '@playwright/test';
import { ENV } from '../config/env';

/**
 * Browser instance: ONE browser per worker process (cheap to launch once),
 * a fresh isolated context + page per scenario (clean cookies/storage).
 */
let sharedBrowser: Browser | undefined;

const launchers: Record<string, BrowserType> = { chromium, firefox, webkit };

export async function getBrowser(): Promise<Browser> {
  if (!sharedBrowser) {
    sharedBrowser = await launchers[ENV.browser].launch({
      headless: ENV.headless,
      slowMo: ENV.slowMo,
      executablePath: ENV.executablePath,
      // --no-sandbox is needed when running as root in containers/CI
      args: ENV.browser === 'chromium' ? ['--no-sandbox'] : [],
    });
  }
  return sharedBrowser;
}

export async function closeBrowser(): Promise<void> {
  if (sharedBrowser) {
    await sharedBrowser.close();
    sharedBrowser = undefined;
  }
}

export async function createContext(scenarioName: string): Promise<BrowserContext> {
  const browser = await getBrowser();
  const context = await browser.newContext({
    baseURL: ENV.baseUrl,
    viewport: ENV.viewport,
    ignoreHTTPSErrors: true,
    recordVideo: ENV.video === 'off' ? undefined : { dir: ENV.dirs.videos, size: ENV.viewport },
  });
  context.setDefaultTimeout(ENV.defaultTimeout);
  context.setDefaultNavigationTimeout(ENV.navigationTimeout);
  if (ENV.trace !== 'off') {
    await context.tracing.start({ name: scenarioName, screenshots: true, snapshots: true });
  }
  return context;
}

export async function createPage(context: BrowserContext): Promise<Page> {
  return context.newPage();
}
```

## `src/support/world.ts`
```typescript
import { setWorldConstructor, World, IWorldOptions } from '@cucumber/cucumber';
import { BrowserContext, Page } from '@playwright/test';
import { CommonFunctions } from '../utils/commonFunctions';
import { LoginComponent } from '../components/login.component';
import { InventoryComponent } from '../components/inventory.component';

/**
 * World = the per-scenario "backpack". Hooks create the page, steps read it.
 * Components are built lazily so each scenario gets fresh instances.
 */
export class CustomWorld extends World {
  context!: BrowserContext;
  page!: Page;
  scenarioName = '';

  private _common?: CommonFunctions;
  private _login?: LoginComponent;
  private _inventory?: InventoryComponent;

  constructor(options: IWorldOptions) {
    super(options);
  }

  get common(): CommonFunctions {
    return (this._common ??= new CommonFunctions(this.page));
  }
  get loginComponent(): LoginComponent {
    return (this._login ??= new LoginComponent(this.page, this.common));
  }
  get inventoryComponent(): InventoryComponent {
    return (this._inventory ??= new InventoryComponent(this.page, this.common));
  }
}

setWorldConstructor(CustomWorld);
```
