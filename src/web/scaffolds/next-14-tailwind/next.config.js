// Polling watchOptions are required for Next dev to see file changes
// across a Docker bind mount. Without these, edits on the host don't
// trigger hot-reload inside the container.
module.exports = {
  reactStrictMode: true,
  webpack: (config) => {
    config.watchOptions = {
      poll: 1000,
      aggregateTimeout: 300,
      ignored: ['**/node_modules/**', '**/.next/**', '**/.jarvis/**'],
    };
    return config;
  },
};
