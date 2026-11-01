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
