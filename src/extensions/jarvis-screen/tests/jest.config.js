module.exports = {
  testEnvironment: "jsdom",
  testMatch: ["**/tests/**/*.test.js"],
  rootDir: "..",   // so tests can `require('../actions.js')`
};
