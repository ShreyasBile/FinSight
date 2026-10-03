import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // compile every page at startup: the first page load then has nothing left to build
    // (it used to sit on a blank page for 15-20 s on a busy machine)
    warmup: { clientFiles: ["./src/main.jsx", "./src/App.jsx", "./src/pages/**/*.jsx", "./src/components/**/*.jsx"] },
    // watch only the frontend: by default Vite also watches the Python virtual environments
    // (~83,000 files in ~7,800 folders), and setting that up blocked every request for 30-60 s
    watch: { ignored: ["**/api/**", "**/storage/**", "**/docs/**", "**/projects/**", "**/dist/**", "**/launcher/**"] },
  },
});
