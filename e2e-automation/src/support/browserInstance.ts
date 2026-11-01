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
