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
