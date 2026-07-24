import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    "dist/**",
    "node_modules/**",
    ".audio-venv/**",
    ".mfa-env/**",
    ".mfa-env312/**",
    ".tts-venv/**",
    ".tts-cache/**",
    "models/**",
    "tmp/**",
    "data/**",
    "public/raz-audio/**",
    "RAZ Book/**",
    "RAZ Audio/**",
  ]),
]);

export default eslintConfig;
