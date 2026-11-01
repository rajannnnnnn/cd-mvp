# E2E Automation — Playwright + Cucumber + TypeScript

```
e2e-automation/
├── .env / .env.example        # all settings (browser, URL, timeouts, test data)
├── cucumber.js                # cucumber config + profiles (default, ci)
├── tsconfig.json
└── src/
    ├── features/login.feature            # 1. FEATURE  (Gherkin, plain English)
    ├── steps/login.steps.ts              # 2. STEPS    (glue: Gherkin -> code)
    ├── components/*.component.ts         # 3. COMPONENT (locators + page actions)
    ├── utils/commonFunctions.ts          # 4. COMMON FUNCTIONS (click, type, waits, asserts)
    ├── config/env.ts                     # reads .env, typed config
    └── support/
        ├── setup.ts                      # global BeforeAll/AfterAll, timeouts, folders
        ├── hooks.ts                      # Before/After per scenario (screenshot, trace, video)
        ├── browserInstance.ts            # launch browser, create context/page
        └── world.ts                      # per-scenario state shared with steps
```

## Run
```bash
cd e2e-automation
npm install
npm run install:browsers        # once (downloads Chromium/Firefox/WebKit)
cp .env.example .env            # then edit

npm test                        # all scenarios
npm run test:smoke              # only @smoke
npm run test:headed             # watch the browser
BROWSER=firefox npm test        # other browser
npx cucumber-js --profile ci    # parallel + 1 retry
npm run typecheck
```
Reports: `reports/cucumber-report.html`. Failures: `screenshots/`, `reports/traces/*.zip`
(open with `npx playwright show-trace <zip>`).

## Add a new test
1. Write a scenario in `src/features/*.feature`.
2. Add a component in `src/components/` and expose it via a getter in `support/world.ts`.
3. Add steps in `src/steps/` that call the component.
