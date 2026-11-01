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
