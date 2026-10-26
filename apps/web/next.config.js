const path = require("path");

/** @type {import('next').NextConfig} */
const nextConfig = {
  // An unrelated lockfile at /Users/espee/package-lock.json (outside this repo)
  // makes Next.js infer the wrong workspace root otherwise.
  outputFileTracingRoot: path.join(__dirname, "../.."),
};

module.exports = nextConfig;
