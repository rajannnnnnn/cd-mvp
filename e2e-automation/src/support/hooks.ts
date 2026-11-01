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
