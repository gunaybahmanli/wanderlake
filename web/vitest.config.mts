import { defineConfig } from "vitest/config";

// Unit tests for the pure data helpers and the fixture data (no Next.js, no network).
export default defineConfig({
  test: {
    include: ["lib/**/*.test.ts"],
    environment: "node",
  },
});
