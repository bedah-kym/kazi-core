// basic playwright config
const { devices } = require('@playwright/test');

module.exports = {
    testDir: '.',
    timeout: 30 * 1000,
    expect: { timeout: 5000 },
    workers: 1,
    use: {
        headless: true,
        viewport: { width: 1280, height: 720 }
    },
};
